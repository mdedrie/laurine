from flask import Flask, render_template, request, send_file, redirect, url_for
import xlsxwriter
import pandas as pd
import os
import io
import tempfile
import json

app = Flask(__name__)

# Maximum file size limit (10 MB)
MAX_FILE_SIZE = 10 * 1024 * 1024

# Charger les fournisseurs disponibles (à partir du fichier Excel fourni)
DATA_FILE = 'C:/Users/m.dedrie/Downloads/Liste_retours_Fournisseurs.xls'
if os.path.exists(DATA_FILE):
    df = pd.read_excel(DATA_FILE)
    df['Date'] = pd.to_datetime(df['Date demande']).dt.date
    fournisseurs_list = df['Fournisseur'].unique().tolist()
    min_date = df['Date'].min()
    max_date = df['Date'].max()
else:
    df = pd.DataFrame()
    fournisseurs_list = []
    min_date = None
    max_date = None

# Page d'accueil permettant de sélectionner les fournisseurs et la période
@app.route('/', methods=['GET', 'POST'])
def index():
    selected_fournisseurs = []
    start_date = None
    end_date = None
    table_html = ""
    anonymize_target = None
    filter_competition = False
    result_df = pd.DataFrame()
    chart_data = {}

    if request.method == 'POST':
        # Récupérer les fournisseurs sélectionnés par l'utilisateur
        selected_fournisseurs = request.form.getlist('fournisseur')
        start_date = request.form.get('start_date')
        end_date = request.form.get('end_date')
        anonymize_target = request.form.get('anonymize_target')
        filter_competition = request.form.get('filter_competition') == 'on'

        # Filtrer les données pour n'afficher que les fournisseurs sélectionnés et la période spécifiée
        filtered_df = df[df['Fournisseur'].isin(selected_fournisseurs)]
        if start_date and end_date:
            filtered_df = filtered_df[(pd.to_datetime(filtered_df['Date']) >= pd.to_datetime(start_date)) & (pd.to_datetime(filtered_df['Date']) <= pd.to_datetime(end_date))]

        # Anonymiser les fournisseurs sauf le fournisseur cible
        if anonymize_target:
            fournisseur_mapping = {f: f'Fournisseur {i+1}' for i, f in enumerate(filtered_df['Fournisseur'].unique()) if f != anonymize_target}
            filtered_df['Fournisseur'] = filtered_df['Fournisseur'].apply(lambda x: x if x == anonymize_target else fournisseur_mapping.get(x, x))

        # Convertir les valeurs de la colonne 'Prix' en numérique, en gérant les valeurs 'RSP'
        filtered_df['Prix'] = pd.to_numeric(filtered_df['Prix'].replace('RSP', None), errors='coerce')

        # Filtrer pour ne garder que les lignes où le fournisseur non anonymisé est en concurrence avec d'autres fournisseurs
        if filter_competition and anonymize_target:
            filtered_df = filtered_df[filtered_df.duplicated(subset=['Série', 'Article', 'Granit'], keep=False)]

        # Créer un tableau croisé dynamique pour comparer les prix des fournisseurs
        pivot_table = filtered_df.pivot_table(index=['Série', 'Article', 'Granit'], columns='Fournisseur', values='Prix', aggfunc='min')
        pivot_table = pivot_table.reset_index()

        # Calculer la différence entre le prix du fournisseur et le prix le plus bas
        def calculate_difference(row):
            numeric_row = pd.to_numeric(row, errors='coerce').dropna()
            min_price = numeric_row[numeric_row > 0].min() if not numeric_row.empty else None
            return row.apply(lambda x: f"{x} (-{round((x - min_price) / min_price * 100, 2)}%)" if pd.notna(x) and isinstance(x, (int, float)) and min_price is not None and x > min_price else f"{x} (Meilleur prix)" if pd.notna(x) else '')

        comparison_df = pivot_table.iloc[:, 3:].apply(calculate_difference, axis=1)
        result_df = pd.concat([pivot_table.iloc[:, :3], comparison_df], axis=1)

        # Générer un HTML avec la mise en forme conditionnelle
        def style_cell(value):
            if '(Meilleur prix)' in value:
                return f'<td style="background-color: #b6d7a8; color: black; font-weight: bold">{value}</td>'
            elif '%' in value:
                percentage = float(value.split('(-')[-1].split('%')[0])
                if percentage < 10:
                    return f'<td style="background-color: #fff2cc; color: black">{value}</td>'
                else:
                    return f'<td style="background-color: #f4cccc; color: black">{value}</td>'
            return f'<td>{value}</td>'

        table_html = '<table border="1" style="width: 100%; border-collapse: collapse;">'
        # Ajouter l'en-tête
        table_html += '<tr style="background-color: #d9d9d9;">' + ''.join([f'<th style="padding: 10px;">{col}</th>' for col in result_df.columns]) + '</tr>'
        # Ajouter les lignes de données
        for _, row in result_df.iterrows():
            table_html += '<tr>' + ''.join([style_cell(str(cell)) for cell in row]) + '</tr>'
        table_html += '</table>'

        # Préparer les données pour les graphiques
        chart_data = {
            'bar_chart': {
                'labels': result_df['Article'].tolist(),
                'datasets': [
                    {
                        'label': fournisseur,
                        'data': result_df[fournisseur].tolist() if fournisseur in result_df.columns else []
                    } for fournisseur in selected_fournisseurs
                ]
            },
            'line_chart': {
                'labels': result_df['Date'].tolist() if 'Date' in result_df.columns else [],
                'datasets': [
                    {
                        'label': fournisseur,
                        'data': result_df[result_df['Fournisseur'] == fournisseur]['Prix'].tolist() if fournisseur in result_df.columns else []
                    } for fournisseur in selected_fournisseurs
                ]
            }
        }

    return render_template('dashboard.html', fournisseurs=fournisseurs_list, min_date=min_date, max_date=max_date, selected_fournisseurs=selected_fournisseurs, start_date=start_date, end_date=end_date, table=table_html, anonymize_target=anonymize_target, filter_competition=filter_competition, chart_data=json.dumps(chart_data))

# Route pour exporter le tableau en fichier Excel
@app.route('/export', methods=['POST'])
def export():
    selected_fournisseurs = request.form.getlist('fournisseur')
    start_date = request.form.get('start_date')
    end_date = request.form.get('end_date')
    anonymize_target = request.form.get('anonymize_target')
    filter_competition = request.form.get('filter_competition') == 'on'

    # Filtrer les données pour n'afficher que les fournisseurs sélectionnés et la période spécifiée
    filtered_df = df[df['Fournisseur'].isin(selected_fournisseurs)]
    if start_date and end_date:
        filtered_df = filtered_df[(pd.to_datetime(filtered_df['Date']) >= pd.to_datetime(start_date)) & (pd.to_datetime(filtered_df['Date']) <= pd.to_datetime(end_date))]

    # Anonymiser les fournisseurs sauf le fournisseur cible
    if anonymize_target:
        fournisseur_mapping = {f: f'Fournisseur {i+1}' for i, f in enumerate(filtered_df['Fournisseur'].unique()) if f != anonymize_target}
        filtered_df['Fournisseur'] = filtered_df['Fournisseur'].apply(lambda x: x if x == anonymize_target else fournisseur_mapping.get(x, x))

    # Convertir les valeurs de la colonne 'Prix' en numérique, en gérant les valeurs 'RSP'
    filtered_df['Prix'] = pd.to_numeric(filtered_df['Prix'].replace('RSP', None), errors='coerce')

    # Filtrer pour ne garder que les lignes où le fournisseur non anonymisé est en concurrence avec d'autres fournisseurs
    if filter_competition and anonymize_target:
        filtered_df = filtered_df[filtered_df.duplicated(subset=['Série', 'Article', 'Granit'], keep=False)]

    # Créer un tableau croisé dynamique pour comparer les prix des fournisseurs
    pivot_table = filtered_df.pivot_table(index=['Série', 'Article', 'Granit'], columns='Fournisseur', values='Prix', aggfunc='min')
    pivot_table = pivot_table.reset_index()

    # Calculer la différence entre le prix du fournisseur et le prix le plus bas pour l'export
    def calculate_difference(row):
        numeric_row = pd.to_numeric(row, errors='coerce').dropna()
        min_price = numeric_row.min()
        return row.apply(lambda x: f"{x} (-{round((x - min_price) / min_price * 100, 2)}%)" if pd.notna(x) and isinstance(x, (int, float)) and x > min_price else f"{x} (Meilleur prix)" if pd.notna(x) else '')

    comparison_df = pivot_table.iloc[:, 3:].apply(calculate_difference, axis=1)
    export_df = pd.concat([pivot_table.iloc[:, :3], comparison_df], axis=1)

    # Exporter le tableau croisé dynamique en fichier Excel
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        workbook = writer.book
        worksheet = workbook.add_worksheet('Comparatif Prix')
        writer.sheets['Comparatif Prix'] = worksheet

        # Écrire les données avec le formatage
        for i, col in enumerate(export_df.columns):
            worksheet.write(0, i, col)
            for j, value in enumerate(export_df[col]):
                cell_format = workbook.add_format()
                if '(Meilleur prix)' in str(value):
                    cell_format.set_bg_color('#b6d7a8')
                    cell_format.set_bold(True)
                elif '%' in str(value):
                    percentage = float(value.split('(-')[-1].split('%')[0])
                    if percentage < 10:
                        cell_format.set_bg_color('#fff2cc')
                    else:
                        cell_format.set_bg_color('#f4cccc')
                worksheet.write(j + 1, i, value, cell_format)

        export_df.to_excel(writer, index=False, sheet_name='Comparatif Prix')
        writer.close()
    output.seek(0)

    return send_file(output, mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', as_attachment=True, download_name='Comparatif_Prix.xlsx')

# Route pour gérer le téléchargement de fichiers
@app.route('/upload', methods=['POST'])
def upload():
    if 'file' not in request.files:
        return redirect(url_for('index'))

    file = request.files['file']
    if file.filename == '':
        return redirect(url_for('index'))

    if file:
        if len(file.read()) > MAX_FILE_SIZE:
            return render_template('dashboard.html', error_message="Le fichier est trop volumineux. La taille maximale autorisée est de 10 MB.")
        file.seek(0)  # Reset file pointer after reading

        temp_dir = tempfile.gettempdir()
        file_path = os.path.join(temp_dir, file.filename)
        file.save(file_path)

        global df, fournisseurs_list, min_date, max_date
        df = pd.read_excel(file_path)
        df['Date'] = pd.to_datetime(df['Date demande']).dt.date
        fournisseurs_list = df['Fournisseur'].unique().tolist()
        min_date = df['Date'].min()
        max_date = df['Date'].max()

    return redirect(url_for('index'))

if __name__ == '__main__':
    app.run(debug=True)
