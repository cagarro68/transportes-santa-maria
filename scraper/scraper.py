import os
import json
import datetime
import re
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
    Analisa nevoeiro (FG, BR, VV), teto baixo (OVC/BKN < 300ft) e vento cruzado/rajadas.
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

# 3. MÓDULO VOOS LPAZ (SATA / AZORES AIRLINES + CORRELAÇÃO METAR)
def scrape_flight_cancellations():
    cancellations = []
    hoje = datetime.date.today().strftime("%Y-%m-%d")
    url_sata_alerts = "https://www.azoresairlines.pt/pt-pt/informacoes/avisos-operacionais"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    
    # Obter dados meteorológicos atuais de LPAZ
    metar_data = get_lpaz_metar_conditions()
    
    try:
        response = requests.get(url_sata_alerts, headers=headers, timeout=10)
        if response.status_code == 200:
            soup = BeautifulSoup(response.text, "html.parser")
            texto_pagina = soup.get_text()
            
            if "Santa Maria" in texto_pagina or "SMA" in texto_pagina:
                if "cancelad" in texto_pagina.lower() or "divergid" in texto_pagina.lower():
                    motivo_final = "Operacional / Meteorologia"
                    if metar_data["detetado"]:
                        motivo_final = metar_data["motivo"]
                    
                    detalhes_txt = "Alerta de condicionalismo operacional detetado nos avisos oficiais SATA."
                    if metar_data["raw"]:
                        detalhes_txt += f" METAR LPAZ: {metar_data['raw']}"
                        
                    cancellations.append({
                        "Data": hoje,
                        "Tipo": "Voo",
                        "Identificador": "SATA / Azores Airlines",
                        "Rota": "PDL-SMA / SMA-PDL",
                        "Estado": "Cancelado/Divergido",
                        "Motivo": motivo_final,
                        "Detalhes": detalhes_txt,
                        "FonteDados": "Automático-Python"
                    })
    except Exception as e:
        print(f"Erro ao verificar voos SATA: {e}")
        
    return cancellations

# 4. MÓDULO NAVIOS DE CARGA (PORTOS DOS AÇORES - PT VDP & MUTUALISTA/TRANSINSULAR)
def scrape_ship_cancellations():
    cancellations = []
    hoje = datetime.date.today().strftime("%Y-%m-%d")
    url_portos = "https://www.portosdosacores.pt/escalas"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    
    armadores_alvo = ["Mutualista", "Transinsular", "Monte da Guia", "Furnas", "Ponta do Sol", "Setúbal"]
    
    try:
        response = requests.get(url_portos, headers=headers, timeout=10)
        if response.status_code == 200:
            soup = BeautifulSoup(response.text, "html.parser")
            texto_portos = soup.get_text()
            
            if "Vila do Porto" in texto_portos or "Santa Maria" in texto_portos:
                texto_lower = texto_portos.lower()
                if any(k in texto_lower for k in ["cancelad", "omiss", "adiad", "divergid"]):
                    # Identificar se há menção a algum navio/armador específico
                    navio_detetado = "Navio Porta-Contentores (Mutualista / Transinsular)"
                    for armador in armadores_alvo:
                        if armador.lower() in texto_lower:
                            navio_detetado = f"Navio {armador}"
                            break
                    
                    cancellations.append({
                        "Data": hoje,
                        "Tipo": "Barco",
                        "Identificador": navio_detetado,
                        "Rota": "Açores / Vila do Porto (PT VDP)",
                        "Estado": "Cancelado/Omitido",
                        "Motivo": "Agitação Marítima / Swell / Condições Marítimas",
                        "Detalhes": "Omissão ou alteração de escala detetada no Porto de Vila do Porto.",
                        "FonteDados": "Automático-Python"
                    })
    except Exception as e:
        print(f"Erro ao verificar escalas marítimas: {e}")
        
    return cancellations

# 5. EXECUÇÃO PRINCIPAL E GRAVAÇÃO NA GOOGLE SHEET
def main():
    sheet_id = os.environ.get("GOOGLE_SHEET_ID")
    if not sheet_id:
        raise ValueError("A variável GOOGLE_SHEET_ID não está definida.")
        
    print("A ligar à Google Sheet...")
    client = get_gspread_client()
    sheet = client.open_by_key(sheet_id).sheet1
    
    registos_existentes = sheet.get_all_records()
    chaves_existentes = set(
        f"{r.get('Data')}_{r.get('Tipo')}_{r.get('Identificador')}" for r in registos_existentes
    )
    
    print("A executar scraping de voos (com METAR LPAZ) e navios (Vila do Porto)...")
    novos_registos = []
    novos_registos.extend(scrape_flight_cancellations())
    novos_registos.extend(scrape_ship_cancellations())
    
    linhas_para_inserir = []
    for item in novos_registos:
        chave = f"{item['Data']}_{item['Tipo']}_{item['Identificador']}"
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
