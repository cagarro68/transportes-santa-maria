import os
import json
import datetime
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

# 2. RECOLHA DE AVISOS E VOOS (SATA Air Açores / Azores Airlines)
def scrape_flight_cancellations():
    cancellations = []
    hoje = datetime.date.today().strftime("%Y-%m-%d")
    
    url_sata_alerts = "https://www.azoresairlines.pt/pt-pt/informacoes/avisos-operacionais"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    
    try:
        response = requests.get(url_sata_alerts, headers=headers, timeout=10)
        if response.status_code == 200:
            soup = BeautifulSoup(response.text, "html.parser")
            texto_pagina = soup.get_text()
            if "Santa Maria" in texto_pagina or "SMA" in texto_pagina:
                if "cancelad" in texto_pagina.lower() or "divergid" in texto_pagina.lower():
                    cancellations.append({
                        "Data": hoje,
                        "Tipo": "Voo",
                        "Identificador": "SATA / Azores Airlines",
                        "Rota": "PDL-SMA / SMA-PDL",
                        "Estado": "Cancelado/Atrasado",
                        "Motivo": "Nevoeiro / Condições Atmosféricas",
                        "Detalhes": "Alerta de condicionalismo operacional detetado nos avisos oficiais.",
                        "FonteDados": "Automático-Python"
                    })
    except Exception as e:
        print(f"Erro ao verificar voos SATA: {e}")
        
    return cancellations

# 3. RECOLHA DE ESCALAS DE NAVIOS (Portos dos Açores / MarineTraffic)
def scrape_ship_cancellations():
    cancellations = []
    hoje = datetime.date.today().strftime("%Y-%m-%d")
    
    url_portos = "https://www.portosdosacores.pt/escalas"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    
    try:
        response = requests.get(url_portos, headers=headers, timeout=10)
        if response.status_code == 200:
            soup = BeautifulSoup(response.text, "html.parser")
            texto_portos = soup.get_text()
            if "Vila do Porto" in texto_portos or "Santa Maria" in texto_portos:
                if "cancelad" in texto_portos.lower() or "omiss" in texto_portos.lower() or "adiad" in texto_portos.lower():
                    cancellations.append({
                        "Data": hoje,
                        "Tipo": "Barco",
                        "Identificador": "Navio de Carga / Porta-Contentores",
                        "Rota": "Açores / Vila do Porto",
                        "Estado": "Cancelado/Omitido",
                        "Motivo": "Agitação Marítima / Vaga de Swell",
                        "Detalhes": "Escala cancelada ou omitida no Porto de Vila do Porto.",
                        "FonteDados": "Automático-Python"
                    })
    except Exception as e:
        print(f"Erro ao verificar escalas marítimas: {e}")
        
    return cancellations

# 4. GRAVAÇÃO NA GOOGLE SHEET
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
    
    print("A executar scraping de voos e barcos...")
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
