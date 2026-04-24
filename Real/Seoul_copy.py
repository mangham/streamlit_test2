import streamlit as st
import pandas as pd
import networkx as nx
from shapely import wkt
import folium
from streamlit_folium import st_folium
import geopandas as gpd

st.set_page_config(page_title="서울시 배리어프리 내비", layout="wide")

@st.cache_data
def load_and_preprocess_data():
    # 1. 파일 읽기 (인코딩 방어)
    try:
        df = pd.read_csv('data/Seoul_map.csv', encoding='utf-8')
    except:
        df = pd.read_csv('data/Seoul_map.csv', encoding='cp949')
    
    # 2. 무적의 이름표 (어떤 이름이든 찰떡같이 찾아냅니다)
    rename_dict = {
        '시작노드 ID': 'from_id', 'from_node': 'from_id', 'from_id': 'from_id',
        '종료노드 ID': 'to_id', 'to_node': 'to_id', 'to_id': 'to_id',
        'mean': 'slope_mean', 'slope_mean': 'slope_mean',
        '링크 길이': 'length_m', 'length_m': 'length_m',
        'WKT': 'WKT'
    }
    df.rename(columns={k: v for k, v in rename_dict.items() if k in df.columns}, inplace=True)
    df = df.dropna(subset=['WKT', 'from_id', 'to_id'])
    
    # 3. 데이터 타입 통일 (소수점 에러 방지)
    df['from_id'] = df['from_id'].astype(str).str.replace('.0', '', regex=False)
    df['to_id'] = df['to_id'].astype(str).str.replace('.0', '', regex=False)
    df['slope_mean'] = pd.to_numeric(df['slope_mean'], errors='coerce').fillna(0)
    
    # 🌟 4. 마법의 좌표 변환기 (북극행 버그 완벽 차단!)
    # WKT 문자열을 실제 공간 데이터로 인식시킵니다.
    gdf = gpd.GeoDataFrame(df, geometry=[wkt.loads(g) for g in df['WKT']])
    
    # "지금 이건 위도/경도가 아니라 한국 표준 미터(5179) 좌표야!" 라고 알려줍니다.
    gdf.set_crs("EPSG:5179", inplace=True)
    
    # "자, 이제 구글맵이 알아먹을 수 있는 글로벌 위경도(4326)로 번역해!"
    gdf_4326 = gdf.to_crs("EPSG:4326")
    
    # 번역된 진짜 위경도 좌표를 데이터에 덮어씌웁니다.
    df['WKT'] = gdf_4326.geometry.apply(lambda x: x.wkt)
    
    # 5. 길찾기 비용 함수 (경사도 패널티)
    def calculate_cost(row):
        if row['slope_mean'] >= 15: # 15도 이상은 위험 구간으로 차단
            return 999999
        return row['length_m'] * (1 + row['slope_mean'] / 100.0)
        
    df['cost'] = df.apply(calculate_cost, axis=1)
    return df

@st.cache_resource
def build_network(df):
    G = nx.from_pandas_edgelist(
        df, source='from_id', target='to_id', 
        edge_attr=['length_m', 'slope_mean', 'cost', 'WKT'], 
        create_using=nx.Graph()
    )
    
    node_coords = {}
    for _, row in df.iterrows():
        try:
            geom = wkt.loads(str(row['WKT']))
            coords = list(geom.geoms[0].coords) if geom.geom_type == 'MultiLineString' else list(geom.coords)
            node_coords[row['from_id']] = (coords[0][1], coords[0][0]) # (위도, 경도)
            node_coords[row['to_id']] = (coords[-1][1], coords[-1][0])
        except:
            pass
    return G, node_coords

# ================= UI 화면 구성 =================
st.title("♿ 배리어프리 휠체어 안전 내비게이션")

with st.spinner("지도 데이터를 분석하고 좌표를 번역 중입니다... (최초 1회)"):
    Seoul_map = load_and_preprocess_data()
    if not Seoul_map.empty:
        G, node_coords = build_network(Seoul_map)

if Seoul_map.empty:
    st.error("데이터 로딩에 실패했습니다.")
    st.stop()

with st.sidebar:
    st.header("📍 경로 검색")
    # 샘플 ID (이제 에러 안 납니다)
    sample_start = Seoul_map['from_id'].iloc[0]
    sample_end = Seoul_map['to_id'].iloc[len(Seoul_map)//5] 
    
    start_node = st.text_input("출발지 (노드 ID)", value=str(sample_start))
    end_node = st.text_input("도착지 (노드 ID)", value=str(sample_end))
    search_btn = st.button("안전 경로 찾기", type="primary")

if search_btn:
    try:
        path = nx.shortest_path(G, source=start_node, target=end_node, weight='cost')
        
        path_wkt = []
        total_len = 0
        total_slope = 0
        
        for i in range(len(path) - 1):
            edge = G.edges[path[i], path[i+1]]
            path_wkt.append(edge['WKT'])
            total_len += edge['length_m']
            total_slope += edge['slope_mean']
            
        avg_slope = total_slope / (len(path) - 1) if len(path) > 1 else 0
        
        st.success("✅ 탐색 성공!")
        col1, col2, col3 = st.columns(3)
        col1.metric("총 이동 거리", f"{total_len:.1f} m")
        col2.metric("평균 경사도", f"{avg_slope:.2f} 도")
        col3.metric("통과 교차로", f"{len(path)} 개")
        
        # 지도 그리기 (이미 번역이 끝났으므로 바로 4326으로 인식)
        gdf_path = gpd.GeoDataFrame(geometry=[wkt.loads(w) for w in path_wkt], crs="EPSG:4326")
        
        start_coord = node_coords.get(start_node, (37.5665, 126.9780))
        m = folium.Map(location=start_coord, zoom_start=15, tiles='CartoDB Positron')
        
        folium.GeoJson(
            gdf_path,
            style_function=lambda x: {'color': '#0052cc', 'weight': 8, 'opacity': 0.8}
        ).add_to(m)
        
        folium.Marker(node_coords.get(start_node), popup='출발', icon=folium.Icon(color='green', icon='play')).add_to(m)
        folium.Marker(node_coords.get(end_node), popup='도착', icon=folium.Icon(color='red', icon='stop')).add_to(m)
        
        # 화면 깜빡임 방지용 코드
        st_folium(m, width=1000, height=600, returned_objects=[])
        
    except nx.NetworkXNoPath:
        st.error("❌ 두 지점을 연결하는 보행 경로가 없습니다.")
    except nx.NodeNotFound:
        st.error("❌ 입력하신 출발지 또는 도착지 번호가 존재하지 않습니다.")
    except Exception as e:
        st.error(f"❌ 알 수 없는 오류: {e}")