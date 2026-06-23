from dataclasses import dataclass
from typing import Tuple

# ==========================================
# 1. 오픈파일럿 시스템 기본 색상 정의
# ==========================================
AMBER = (255, 193, 7, 255)
BLUE = (0, 122, 255, 255)
BLUE_SOFT = (10, 132, 255, 255)
GREEN = (40, 200, 100, 255)
RED = (255, 50, 50, 255)
TEXT = (255, 255, 255, 255)
WHITE = (255, 255, 255, 255)

# ==========================================
# 2. 레이더 및 화면 렌더링 상태 모드 상수 정의
# ==========================================
CLUSTER_RADAR_INFO_NONE = 0
CLUSTER_RADAR_INFO_ALL_SPEED = 1
CLUSTER_RADAR_INFO_ALL_SPEED_DISTANCE = 2
CLUSTER_RADAR_INFO_VEHICLE_SPEED = 3
CLUSTER_RADAR_INFO_VEHICLE_SPEED_DISTANCE = 4

CLUSTER_RADAR_SOURCE_COLOR_BY_SOURCE = 1

CLUSTER_SCREEN_MODE_DEBUG = 1
CLUSTER_SCREEN_MODE_DEBUG_GRAPH = 2
CLUSTER_SCREEN_MODE_DEBUG_GRAPH_RIGHT = 3
CLUSTER_SCREEN_MODE_NAVI_DEBUG = 4
CLUSTER_SCREEN_MODE_DEBUG_SYSTEM = 5

DESIGN_HEIGHT = 480
DESIGN_WIDTH = 1920

EGO_FORWARD_M = 1.5
MAX_ACCEL_MPS2 = 4.0
MAX_SPEED_KPH = 250.0

# ==========================================
# 3. 테마 클래스 뼈대 정의
# ==========================================
@dataclass
class ClusterTheme:
    name: str
    is_dark: bool
    bg: Tuple[int, ...]
    panel_bg: Tuple[int, ...]
    text: Tuple[int, ...]
    muted: Tuple[int, ...]
    faint: Tuple[int, ...]
    road: Tuple[int, ...]
    road_edge: Tuple[int, ...]
    lane_marking_border: Tuple[int, ...]
    road_edge_backing: Tuple[int, ...]
    path_shadow: Tuple[int, ...]
    path_uncertainty: Tuple[int, ...]
    path_body: Tuple[int, ...]
    path_highlight: Tuple[int, ...]
    world_label_shadow: Tuple[int, ...]
    world_label_text: Tuple[int, ...]
    clock_bg: Tuple[int, ...]
    clock_outline: Tuple[int, ...]
    clock_text: Tuple[int, ...]
    gauge_bg: Tuple[int, ...]
    gauge_midline: Tuple[int, ...]
    inactive_signal_fill: Tuple[int, ...]
    inactive_signal_outline: Tuple[int, ...]
    route_panel_bg: Tuple[int, ...]
    route_video_bg: Tuple[int, ...]
    route_video_status: Tuple[int, ...]
    primary_vehicle: Tuple[int, ...]
    model_vehicle: Tuple[int, ...]
    default_vehicle: Tuple[int, ...]

# ==========================================
# 4. 커스텀 테마 세팅 (EV6 튜닝 및 반투명 UI 적용 완료)
# ==========================================
LIGHT_CLUSTER_THEME = ClusterTheme(
    name="light",
    is_dark=False,
    bg=(255, 255, 255),
    panel_bg=(248, 249, 250),
    text=(10, 10, 10),
    muted=(140, 145, 150),
    faint=(230, 230, 235),
    road=(255, 255, 255),
    road_edge=(200, 204, 208),
    lane_marking_border=(180, 185, 190, 120),
    road_edge_backing=(255, 255, 255, 0),
    path_shadow=(0, 0, 0, 20),
    path_uncertainty=(0, 210, 160, 40),
    path_body=(0, 200, 110, 130),
    path_highlight=(180, 255, 220, 180),
    world_label_shadow=(255, 255, 255),
    world_label_text=(50, 50, 50),
    clock_bg=(240, 242, 245, 200),
    clock_outline=(220, 225, 230, 150),
    clock_text=(30, 30, 30),
    gauge_bg=(240, 242, 245),
    gauge_midline=(200, 200, 200),
    inactive_signal_fill=(240, 240, 245, 200),
    inactive_signal_outline=(220, 220, 225),
    route_panel_bg=(255, 255, 255),
    route_video_bg=(10, 10, 10),
    route_video_status=(200, 200, 200),
    primary_vehicle=(85, 90, 95),  # 커스텀 EV6 명도 최적화
    model_vehicle=(220, 225, 230),
    default_vehicle=(200, 205, 210),
)

DARK_CLUSTER_THEME = ClusterTheme(
    name="dark",
    is_dark=True,
    bg=(12, 14, 18),
    panel_bg=(24, 26, 30),
    text=(250, 250, 250),
    muted=(120, 125, 130),
    faint=(45, 50, 55),
    road=(12, 14, 18),
    road_edge=(70, 75, 80),
    lane_marking_border=(100, 105, 110, 120),
    road_edge_backing=(0, 0, 0, 0),
    path_shadow=(0, 0, 0, 100),
    path_uncertainty=(0, 180, 140, 50),
    path_body=(0, 200, 110, 130),
    path_highlight=(180, 255, 220, 180),
    world_label_shadow=(12, 14, 18),
    world_label_text=(220, 220, 220),
    clock_bg=(30, 35, 40, 200),
    clock_outline=(50, 55, 60, 150),
    clock_text=(250, 250, 250),
    gauge_bg=(25, 30, 35),
    gauge_midline=(60, 65, 70),
    inactive_signal_fill=(30, 35, 40, 200),
    inactive_signal_outline=(50, 55, 60),
    route_panel_bg=(18, 20, 24),
    route_video_bg=(5, 5, 5),
    route_video_status=(150, 150, 150),
    primary_vehicle=(85, 90, 95),  # 커스텀 EV6 명도 최적화
    model_vehicle=(160, 165, 170),
    default_vehicle=(140, 145, 150),
)

# ==========================================
# 5. UI 테마 및 모드 설정 함수
# ==========================================
def normalize_cluster_screen_mode(screen_mode: int) -> int:
    return int(screen_mode)

def normalize_cluster_theme_mode(theme_mode: str) -> str:
    if theme_mode and theme_mode.lower() in ("light", "dark"):
        return theme_mode.lower()
    return "dark"

def current_cluster_theme(theme_mode: str) -> ClusterTheme:
    if theme_mode == "light":
        return LIGHT_CLUSTER_THEME
    return DARK_CLUSTER_THEME