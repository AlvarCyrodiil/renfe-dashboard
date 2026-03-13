import dash
from dash import dcc, html, Input, Output, State
import plotly.graph_objects as go
import networkx as nx
import pandas as pd
import random
import json

# Cargar datos reales
stops = pd.read_csv('stops.txt')
stop_times = pd.read_csv('stop_times_madrid.csv')

stops.columns = stops.columns.str.strip()
stop_times.columns = stop_times.columns.str.strip()
stops['stop_name'] = stops['stop_name'].str.strip()

# Construir grafo
stop_times = stop_times.sort_values(['trip_id', 'stop_sequence'])
edges = stop_times.merge(stop_times, on='trip_id').query('stop_sequence_x + 1 == stop_sequence_y')[['stop_id_x', 'stop_id_y']]
edges = edges.merge(stops[['stop_id', 'stop_name']], left_on='stop_id_x', right_on='stop_id').rename(columns={'stop_name': 'name_x'}).drop('stop_id', axis=1)
edges = edges.merge(stops[['stop_id', 'stop_name']], left_on='stop_id_y', right_on='stop_id').rename(columns={'stop_name': 'name_y'}).drop('stop_id', axis=1)

G = nx.from_pandas_edgelist(edges, source='name_x', target='name_y')
pos = {row['stop_name']: (row['stop_lon'], row['stop_lat']) for _, row in stops.iterrows()}

# Subgrafo Madrid
componentes = list(nx.connected_components(G))
madrid_component = next(comp for comp in componentes if 'Madrid-Atocha Cercanías' in comp)
G_madrid = G.subgraph(madrid_component).copy()
pos_madrid = {node: pos[node] for node in G_madrid.nodes() if node in pos}

node_x = [pos_madrid[n][0] for n in G_madrid.nodes() if n in pos_madrid]
node_y = [pos_madrid[n][1] for n in G_madrid.nodes() if n in pos_madrid]
node_names = [n for n in G_madrid.nodes() if n in pos_madrid]

edge_x, edge_y = [], []
for u, v in G_madrid.edges():
    if u in pos_madrid and v in pos_madrid:
        edge_x += [pos_madrid[u][0], pos_madrid[v][0], None]
        edge_y += [pos_madrid[u][1], pos_madrid[v][1], None]

def simular_SIS_intensidad(nodo_inicial, retraso_inicial=20, decay=0.7,
                            prob_recuperacion=0.2, umbral=1.0, pasos=50):
    estado = {n: 0.0 for n in G_madrid.nodes()}
    estado[nodo_inicial] = float(retraso_inicial)
    historial = [dict(estado)]
    for _ in range(pasos):
        nuevo = dict(estado)
        for node in G_madrid.nodes():
            if estado[node] > 0:
                if random.random() < prob_recuperacion:
                    nuevo[node] = 0.0
                else:
                    retraso_heredado = estado[node] * decay
                    if retraso_heredado > umbral:
                        for vecino in G_madrid.neighbors(node):
                            if estado[vecino] < retraso_heredado:
                                if random.random() < 0.6:
                                    nuevo[vecino] = retraso_heredado
        estado = nuevo
        historial.append(dict(estado))
    return historial

def retraso_a_color(minutos, max_retraso=20):
    if minutos <= 0:
        return 'steelblue'
    ratio = min(minutos / max_retraso, 1.0)
    if ratio < 0.5:
        r = int(255 * ratio * 2)
        g = int(180 * ratio * 2)
        b = int(70 * (1 - ratio * 2))
    else:
        r = 255
        g = int(180 * (1 - (ratio - 0.5) * 2))
        b = 0
    return f'rgb({r},{g},{b})'

app = dash.Dash(__name__)
server = app.server

app.layout = html.Div([
    html.H2("RENFE Madrid — Delay Propagation",
            style={'textAlign': 'center', 'fontFamily': 'Arial'}),
    html.Div([
        html.Div([
            html.Label("Severity (initial delay in minutes):", style={'fontFamily': 'Arial'}),
            dcc.Slider(id='seriedad', min=5, max=30, step=5, value=20,
                       marks={i: f'{i}min' for i in range(5, 31, 5)}),
        ], style={'width': '45%', 'display': 'inline-block', 'padding': '10px'}),
        html.Div([
            html.Label("Attenuation (decay per hop):", style={'fontFamily': 'Arial'}),
            dcc.Slider(id='decay', min=0.3, max=0.9, step=0.1, value=0.7,
                       marks={round(i,1): str(round(i,1)) for i in [0.3,0.4,0.5,0.6,0.7,0.8,0.9]}),
        ], style={'width': '45%', 'display': 'inline-block', 'padding': '10px'}),
    ], style={'textAlign': 'center'}),
    html.P("Click on a station to start, then press ▶ Play",
           style={'textAlign': 'center', 'fontFamily': 'Arial', 'color': 'gray'}),
    html.Div([
        html.Span("🔵 No delay  ", style={'fontFamily': 'Arial', 'marginRight': '15px'}),
        html.Span("🟡 Moderate  ", style={'fontFamily': 'Arial', 'marginRight': '15px'}),
        html.Span("🔴 Severe delay", style={'fontFamily': 'Arial'}),
    ], style={'textAlign': 'center', 'paddingBottom': '10px'}),
    dcc.Graph(id='network-graph', style={'height': '600px'}),
    html.Div(id='info', style={'textAlign': 'center', 'fontFamily': 'Arial', 'fontSize': '14px', 'padding': '10px'}),
    html.Div([
        html.Button('▶ Play', id='play-button', n_clicks=0,
                    style={'marginRight': '20px', 'padding': '8px 20px', 'fontSize': '14px', 'cursor': 'pointer'}),
        html.Button('↺ Reset', id='reset-button', n_clicks=0,
                    style={'padding': '8px 20px', 'fontSize': '14px', 'cursor': 'pointer'}),
    ], style={'textAlign': 'center', 'paddingBottom': '10px'}),
    html.Div([
        dcc.Slider(id='step-slider', min=0, max=50, step=1, value=0,
                   marks={i: str(i) for i in range(0, 51, 5)}),
    ], style={'width': '80%', 'margin': 'auto', 'paddingBottom': '20px'}),
    dcc.Interval(id='interval', interval=300, n_intervals=0, disabled=True),
    dcc.Store(id='historial-store'),
    dcc.Store(id='playing', data=False)
])

@app.callback(
    Output('historial-store', 'data'),
    Output('info', 'children'),
    Output('step-slider', 'value'),
    Input('network-graph', 'clickData'),
    Input('reset-button', 'n_clicks'),
    State('seriedad', 'value'),
    State('decay', 'value')
)
def run_simulation(clickData, reset_clicks, seriedad, decay):
    ctx = dash.callback_context
    if not ctx.triggered:
        return None, "Click a station to start", 0
    trigger = ctx.triggered[0]['prop_id']
    if 'reset-button' in trigger:
        return None, "Click a station to start", 0
    if clickData is None:
        return None, "Click a station to start", 0
    try:
        nodo = clickData['points'][0]['text']
    except (KeyError, IndexError):
        return None, "Click directly on a station dot", 0
    historial = simular_SIS_intensidad(nodo, retraso_inicial=seriedad, decay=decay, pasos=50)
    info = f" Origin: {nodo} | Initial delay: {seriedad} min | Decay: {decay} | Press ▶ Play"
    return json.dumps(historial), info, 0

@app.callback(
    Output('interval', 'disabled'),
    Output('playing', 'data'),
    Output('play-button', 'children'),
    Input('play-button', 'n_clicks'),
    Input('reset-button', 'n_clicks'),
    State('playing', 'data')
)
def toggle_play(play_clicks, reset_clicks, is_playing):
    ctx = dash.callback_context
    if not ctx.triggered:
        return True, False, '▶ Play'
    trigger = ctx.triggered[0]['prop_id']
    if 'reset-button' in trigger:
        return True, False, '▶ Play'
    new_playing = not is_playing
    label = '⏸ Pause' if new_playing else '▶ Play'
    return not new_playing, new_playing, label

@app.callback(
    Output('step-slider', 'value', allow_duplicate=True),
    Input('interval', 'n_intervals'),
    State('step-slider', 'value'),
    State('historial-store', 'data'),
    prevent_initial_call=True
)
def advance_step(n_intervals, current_step, historial_json):
    if historial_json is None:
        return 0
    next_step = current_step + 1
    if next_step > 50:
        return 50
    return next_step

@app.callback(
    Output('network-graph', 'figure'),
    Input('step-slider', 'value'),
    Input('historial-store', 'data'),
    State('seriedad', 'value')
)
def update_graph(step, historial_json, max_retraso):
    edge_trace = go.Scatter(x=edge_x, y=edge_y, mode='lines',
                            line=dict(width=1.5, color='lightgray'), hoverinfo='none')
    if historial_json:
        historial = json.loads(historial_json)
        step = min(step, len(historial) - 1)
        estado = historial[step]
        colors = [retraso_a_color(estado.get(n, 0), max_retraso) for n in node_names]
        sizes = [12 + min(estado.get(n, 0), max_retraso) * 0.5 for n in node_names]
        hover = [f"{n}<br>Delay: {estado.get(n, 0):.1f} min" for n in node_names]
        n_afectados = sum(1 for v in estado.values() if v > 0)
        retraso_max = max(estado.values())
        title = f"Step {step}/50 — Affected: {n_afectados} stations | Max delay: {retraso_max:.1f} min"
    else:
        colors = ['steelblue'] * len(node_names)
        sizes = [10] * len(node_names)
        hover = node_names
        title = "Click a station to start"
    node_trace = go.Scatter(
        x=node_x, y=node_y, mode='markers+text',
        marker=dict(size=sizes, color=colors, line=dict(width=1, color='white')),
        text=node_names, textposition='top center', textfont=dict(size=7),
        hovertext=hover, hoverinfo='text'
    )
    fig = go.Figure(data=[edge_trace, node_trace],
                    layout=go.Layout(
                        title=dict(text=title, x=0.5, font=dict(size=14)),
                        showlegend=False, hovermode='closest',
                        xaxis=dict(showgrid=False, zeroline=False, showticklabels=False),
                        yaxis=dict(showgrid=False, zeroline=False, showticklabels=False),
                        plot_bgcolor='white', margin=dict(l=20, r=20, t=40, b=20)
                    ))
    return fig

if __name__ == '__main__':
    app.run(debug=False)
