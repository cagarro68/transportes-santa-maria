import os
import json
import datetime
import re
import urllib.parse
import xml.etree.ElementTree as ET
import requests
from bs4 import BeautifulSoup
import gspread
from oauth2client.service_account import ServiceAccountCredentials

# 1. AUTENTICAÇÃO NA GOOGLE SHEET VIA SERVICE ACCOUNT
def get_gspread_client():
    service_account_info = os.environ.get("GCP_SERVICE_ACCOUNT_KEY")
    if not service_account_info:
        raise ValueError("A variável GCP_SERVICE_ACCOUNT_KEY não está definida.")
    
    creds_dict = json.loads(service_account_info)
    scope = [
        "https://spreadsheets.google.com/feeds",
        "https://www.googleapis.com/auth/drive"
    ]
    creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
    return gspread.authorize(creds)

# 2. CONSULTA METEOROLÓGICA METAR (AEROPORTO DE SANTA MARIA - LPAZ)
def get_lpaz_metar_conditions():
    """
    Consulta a API NOAA/tgftp para obter o METAR atual de LPAZ.
    Analisa nevoeiro (FG, BR, VV), teto baixo (OVC/BKN < 300ft) e vento com rajadas.
    """
    url = "https://tgftp.nws.noaa.gov/data/observations/metar/stations/LPAZ.TXT"
    headers = {"User-Agent": "Mozilla/5.0"}
    metar_info = {"detetado": False, "motivo": "", "raw": ""}
    
    try:
        response = requests.get(url, headers=headers, timeout=10)
        if response.status_code == 200:
            lines = response.text.strip().split('\n')
            if len(lines) >= 2:
                metar_raw = lines[1]
                metar_info["raw"] = metar_raw
                motivos = []
                
                # Nevoeiro / Visibilidade reduzida
                if any(code in metar_raw for code in [" FG", " BR", " VV"]):
                    motivos.append("Nevoeiro / Visibilidade Reduzida")
                
                # Teto baixo de nuvens (ex: OVC002, BKN001)
                teto_match = re.search(r'(BKN|OVC)00[1-3]', metar_raw)
                if teto_match:
                    motivos.append(f"Teto Baixo de Nuvens ({teto_match.group(0)})")
                
                # Vento com rajadas (G = Gusts, ex: 18025G40KT)
                gust_match = re.search(r'\d{5}G\d{2}KT', metar_raw)
                if gust_match:
                    motivos.append(f"Vento Forte com Rajadas ({gust_match.group(0)})")
                
                if motivos:
                    metar_info["detetado"] = True
                    metar_info["motivo"] = " | ".join(motivos)
    except Exception as e:
        print(f"Aviso METAR LPAZ: Não foi possível obter dados meteorológicos: {e}")
        
    return metar_info

# 3. PESQUISA DE NOTÍCIAS REGIONAIS (RTP AÇORES, AÇORIANO ORIENTAL, GOOGLE NEWS RSS)
def fetch_rss_alerts(query_str):
    """
    Lê notícias publicadas nas últimas 24h (when:1d) via Google News RSS em PT-PT.
    Captura notícias da RTP Açores, Açoriano Oriental, Diário dos Açores, etc.
    """
    encoded_q = urllib.parse.quote(f"{query_str} when:1d")
    rss_url = f"https://news.google.com/rss/search?q={encoded_q}&hl=pt-PT&gl=PT&ceid=PT:pt-150"
    headers = {"User-Agent": "Mozilla/5.0"}
    artigos = []

    try:
        resp = requests.get(rss_url, headers=headers, timeout=10)
        if resp.status_code == 200:
            root = ET.fromstring(resp.content)
            for item in root.findall(".//item")[:5]:
                titulo = item.find("title").text if item.find("title") is not None else ""
                fonte = item.find("source").text if item.find("source") is not None else "Imprensa Regional"
                artigos.append({"titulo": titulo, "fonte": fonte})
    except Exception as e:
        print(f"Aviso RSS ({query_str}): {e}")

    return artigos

# 4. MÓDULO VOOS LPAZ (NOTÍCIAS + AVISOS SATA + METAR)
def scrape_flight_cancellations():
    cancellations = []
    hoje = datetime.date.today().strftime("%Y-%m-%d")
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    
    metar_data = get_lpaz_metar_conditions()
    
    # A) Verificar notícias recentes de voos nos Açores / Santa Maria
    queries_voos = [
        '"Santa Maria" voos cancelados OR atrasos OR nevoeiro',
        '"Açores" voos cancelados nevoeiro OR vento'
    ]
    
    noticias_encontradas = []
    for q in queries_voos:
        noticias_encontradas.extend(fetch_rss_alerts(q))
        
    for art in noticias_encontradas:
        titulo_lower = art["titulo"].lower()
        # Confirma se fala de voos/aeroporto e cancelamento/atraso/nevoeiro
        if any(p in titulo_lower for p in ["voo", "voos", "aeroporto", "sata"]) and \
           any(c in titulo_lower for c in ["cancelad", "atraso", "divergid", "condicionad", "nevoeiro"]):
            
            motivo = "Meteorologia / Operacional"
            if "nevoeiro" in titulo_lower:
                motivo = "Nevoeiro"
            elif "vento" in titulo_lower:
                motivo = "Vento Forte"
            elif metar_data["detetado"]:
                motivo = metar_data["motivo"]

            detalhes = f"Notícia ({art['fonte']}): {art['titulo']}"
            if metar_data["raw"]:
                detalhes += f" | METAR LPAZ: {metar_data['raw']}"

            cancellations.append({
                "Data": hoje,
                "Tipo": "Voo",
                "Identificador": f"SATA / LPAZ ({art['fonte']})",
                "Rota": "LPAZ (Santa Maria)",
                "Estado": "Cancelado/Atrasado",
                "Motivo": motivo,
                "Detalhes": detalhes,
                "FonteDados": art["fonte"]
            })
            break  # Evita inserir múltiplas notícias sobre o mesmo evento do dia

    # B) Se não saiu notícia, mas o METAR de LPAZ detetou nevoeiro cerrado / teto muito baixo
    if not cancellations and metar_data["detetado"] and "Nevoeiro" in metar_data["motivo"]:
        cancellations.append({
            "Data": hoje,
            "Tipo": "Voo",
            "Identificador": "Condicionalismo LPAZ (METAR)",
            "Rota": "LPAZ (Santa Maria)",
            "Estado": "Condicionado",
            "Motivo": metar_data["motivo"],
            "Detalhes": f"Condições meteorológicas adversas registadas no Aeroporto de Santa Maria. METAR: {metar_data['raw']}",
            "FonteDados": "NOAA / METAR LPAZ"
        })

    return cancellations

# 5. MÓDULO NAVIOS DE CARGA (PORTOS DOS AÇORES + NOTÍCIAS MARÍTIMAS)
def scrape_ship_cancellations():
    cancellations = []
    hoje = datetime.date.today().strftime("%Y-%m-%d")
    url_portos = "https://www.portosdosacores.pt/escalas"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    
    armadores_alvo = ["Mutualista", "Transinsular", "Monte da Guia", "Furnas", "Ponta do Sol", "Setúbal"]
    
    # A) Portal Portos dos Açores
    try:
        response = requests.get(url_portos, headers=headers, timeout=10)
        if response.status_code == 200:
            soup = BeautifulSoup(response.text, "html.parser")
            texto_portos = soup.get_text()
            
            if "Vila do Porto" in texto_portos or "Santa Maria" in texto_portos:
                texto_lower = texto_portos.lower()
                if any(k in texto_lower for k in ["cancelad", "omiss", "adiad", "divergid"]):
                    navio_detetado = "Navio Porta-Contentores"
                    for armador in armadores_alvo:
                        if armador.lower() in texto_lower:
                            navio_detetado = f"Navio {armador}"
                            break
                    
                    cancellations.append({
                        "Data": hoje,
                        "Tipo": "Barco",
                        "Identificador": navio_detetado,
                        "Rota": "Porto de Vila do Porto (PT VDP)",
                        "Estado": "Cancelado/Omitido",
                        "Motivo": "Agitação Marítima / Condições Marítimas",
                        "Detalhes": "Omissão ou alteração de escala detetada no portal Portos dos Açores.",
                        "FonteDados": "Portos dos Açores"
                    })
    except Exception as e:
        print(f"Erro ao verificar escalas marítimas: {e}")

    # B) Notícias sobre navios / carga em Santa Maria / Vila do Porto
    if not cancellations:
        noticias_barcos = fetch_rss_alerts('"Vila do Porto" OR "Santa Maria" navio OR escala cancelada OR omitida OR marítima')
        for art in noticias_barcos:
            titulo_lower = art["titulo"].lower()
            if any(p in titulo_lower for p in ["navio", "escala", "barco", "mercadoria", "transinsular", "mutualista"]) and \
               any(c in titulo_lower for c in ["cancelad", "omitid", "adiad", "agitação marítima", "mau tempo"]):
                cancellations.append({
                    "Data": hoje,
                    "Tipo": "Barco",
                    "Identificador": f"Carga Marítima ({art['fonte']})",
                    "Rota": "Porto de Vila do Porto (PT VDP)",
                    "Estado": "Cancelado/Omitido",
                    "Motivo": "Agitação Marítima",
                    "Detalhes": f"Notícia ({art['fonte']}): {art['titulo']}",
                    "FonteDados": art["fonte"]
                })
                break
        
    return cancellations

# 6. EXECUÇÃO PRINCIPAL E GRAVAÇÃO NA GOOGLE SHEET
def main():
    sheet_id = os.environ.get("GOOGLE_SHEET_ID")
    if not sheet_id:
        raise ValueError("A variável GOOGLE_SHEET_ID não está definida.")
        
    print("A ligar à Google Sheet...")
    client = get_gspread_client()
    sheet = client.open_by_key(sheet_id).sheet1
    
    registos_existentes = sheet.get_all_records()
    chaves_existentes = set(
        f"{r.get('Data')}_{r.get('Tipo')}" for r in registos_existentes
    )
    
    print("A executar scraping de voos (LPAZ / Notícias / METAR) e navios (Vila do Porto)...")
    novos_registos = []
    novos_registos.extend(scrape_flight_cancellations())
    novos_registos.extend(scrape_ship_cancellations())
    
    linhas_para_inserir = []
    for item in novos_registos:
        # Evita duplicar vários registos de Voo ou Barco no mesmo dia
        chave = f"{item['Data']}_{item['Tipo']}"
        if chave not in chaves_existentes:
            linhas_para_inserir.append([
                item["Data"],
                item["Tipo"],
                item["Identificador"],
                item["Rota"],
                item["Estado"],
                item["Motivo"],
                item["Detalhes"],
                item["FonteDados"]
            ])
            chaves_existentes.add(chave)
            
    if linhas_para_inserir:
        sheet.append_rows(linhas_para_inserir)
        print(f"Sucesso: {len(linhas_para_inserir)} novos registos inseridos!")
    else:
        print("Nenhum novo cancelamento detetado no dia de hoje.")

if __name__ == "__main__":
    main()
