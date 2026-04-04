import requests
import os

url = 'http://127.0.0.1:5000/api/despacho-masivo'
file_path = 'plantilla_carga_masiva_stock.xlsx'

if not os.path.exists(file_path):
    print("El archivo no existe.")
else:
    with open(file_path, 'rb') as f:
        files = {'file': (file_path, f, 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')}
        data = {'modo': 'reemplazar'}
        response = requests.post(url, files=files, data=data)
        print("Status Code:", response.status_code)
        try:
            print("Response:", response.json())
        except:
            print("Response text:", response.text)
