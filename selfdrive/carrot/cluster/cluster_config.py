from __future__ import annotations

import time
from dataclasses import dataclass

DESIGN_WIDTH = 1920
DESIGN_HEIGHT = 480

Color3 = tuple[int, int, int]
Color4 = tuple[int, int, int, int]


@dataclass(frozen=True, slots=True)
class ClusterTheme:
    name: str
    is_dark: bool
    bg: Color3
    panel_bg: Color3
    text: Color3
    muted: Color3
    faint: Color3
    road: Color3
    road_edge: Color3
    lane_marking_border: Color4
    road_edge_backing: Color4
    path_shadow: Color4
    path_uncertainty: Color4
    path_body: Color4
    path_highlight: Color4
    world_label_shadow: Color3
    world_label_text: Color3
    clock_bg: Color4
    clock_outline: Color4
    clock_text: Color3
    gauge_bg: Color3
    gauge_midline: Color3
    inactive_signal_fill: Color4
    inactive_signal_outline: Color3
    route_panel_bg: Color3
    route_video_bg: Color3
    route_video_status: Color3
    primary_vehicle: Color3
    model_vehicle: Color3
    default_vehicle: Color3


CLUSTER_THEME_AUTO = 0
CLUSTER_THEME_DARK = 1
CLUSTER_THEME_LIGHT = 2
CLUSTER_ENCODER_AUTO = 0
CLUSTER_ENCODER_JPEG = 1
CLUSTER_ENCODER_HARDWARE = 2
CLUSTER_ENCODER_SOFTWARE = 3
CLUSTER_HUD_PARAM = "ClusterHud"
CLUSTER_HUD_DEBUG_PARAM = "ClusterHudDebug"
CLUSTER_BRIGHTNESS_PARAM = "ClusterHudBrightness"
CLUSTER_ENCODER_PARAM = "ClusterHudEncoder"
CLUSTER_CORE_MODE_PARAM = "ClusterHudCoreMode"
CLUSTER_PRIORITY_PARAM = "ClusterHudPriority"
CLUSTER_THEME_PARAM = "ClusterHudTheme"
CLUSTER_LIVE_FPS_PARAM = "ClusterHudLiveFps"
CLUSTER_RADAR_INFO_PARAM = "ClusterHudRadarInfo"
CLUSTER_RADAR_DISPLAY_PARAM = "ClusterHudRadarDisplay"
CLUSTER_RADAR_SOURCE_COLOR_PARAM = "ClusterHudRadarSourceColor"
CLUSTER_CORE_MODE_DEDICATED = 0
CLUSTER_CORE_MODE_ALL = 1
CLUSTER_PRIORITY_DEFAULT = 10
CLUSTER_PRIORITY_MIN = 1
CLUSTER_PRIORITY_MAX = 99
CLUSTER_CAMERA_VIEW_MODE_DEFAULT = 0
CLUSTER_CAMERA_VIEW_MODE_EGO_BOTTOM = 1
CLUSTER_CAMERA_VIEW_MODE_PARAM = "ClusterHudCameraViewMode"
CLUSTER_SCREEN_MODE_DEFAULT = 0
CLUSTER_SCREEN_MODE_DEBUG = 1
CLUSTER_SCREEN_MODE_DEBUG_SYSTEM = 2
CLUSTER_SCREEN_MODE_DEBUG_GRAPH = 3
CLUSTER_SCREEN_MODE_DEBUG_GRAPH_RIGHT = 4
CLUSTER_SCREEN_MODE_NAVI_DEBUG = 5
CLUSTER_SCREEN_MODE_PARAM = "ClusterHudScreenMode"
CLUSTER_RADAR_INFO_NONE = 0
CLUSTER_RADAR_INFO_VEHICLE_SPEED = 1
CLUSTER_RADAR_INFO_VEHICLE_SPEED_DISTANCE = 2
CLUSTER_RADAR_INFO_ALL_SPEED = 3
CLUSTER_RADAR_INFO_ALL_SPEED_DISTANCE = 4
CLUSTER_RADAR_DISPLAY_MERGED = 0
CLUSTER_RADAR_DISPLAY_DETAIL = 1
CLUSTER_RADAR_SOURCE_COLOR_DEFAULT = 0
CLUSTER_RADAR_SOURCE_COLOR_BY_SOURCE = 1
SHOW_PLOT_MODE_PARAM = "ShowPlotMode"
AUTO_DARK_START_HOUR = 18
AUTO_LIGHT_START_HOUR = 6
CLUSTER_LIVE_FPS_BY_MODE = {
    0: 0.0,
    1: 10.0,
    2: 20.0,
    3: 30.0,
    4: 40.0,
    5: 50.0,
    6: 60.0,
}

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

def normalize_cluster_theme_mode(value: object) -> str:
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in ("auto", "dark", "light"):
            return normalized
        try:
            value = int(normalized)
        except ValueError:
            return "auto"
    if value == CLUSTER_THEME_DARK:
        return "dark"
    if value == CLUSTER_THEME_LIGHT:
        return "light"
    return "auto"


def normalize_cluster_live_fps(value: object) -> float:
    if isinstance(value, str):
        normalized = value.strip()
        try:
            value = int(normalized)
        except ValueError:
            return 0.0
    try:
        mode = int(value)
    except (TypeError, ValueError):
        return 0.0
    return CLUSTER_LIVE_FPS_BY_MODE.get(mode, 0.0)


def normalize_cluster_encoder_mode(value: object) -> int:
    if isinstance(value, str):
        normalized = value.strip().lower()
        aliases = {
            "auto": CLUSTER_ENCODER_AUTO,
            "jpeg": CLUSTER_ENCODER_JPEG,
            "jpg": CLUSTER_ENCODER_JPEG,
            "hardware": CLUSTER_ENCODER_HARDWARE,
            "hw": CLUSTER_ENCODER_HARDWARE,
            "h264": CLUSTER_ENCODER_HARDWARE,
            "native": CLUSTER_ENCODER_HARDWARE,
            "software": CLUSTER_ENCODER_SOFTWARE,
            "sw": CLUSTER_ENCODER_SOFTWARE,
            "ffmpeg": CLUSTER_ENCODER_SOFTWARE,
        }
        if normalized in aliases:
            return aliases[normalized]
        try:
            value = int(normalized)
        except ValueError:
            return CLUSTER_ENCODER_AUTO
    try:
        mode = int(value)
    except (TypeError, ValueError):
        return CLUSTER_ENCODER_AUTO
    if mode in (
        CLUSTER_ENCODER_AUTO,
        CLUSTER_ENCODER_JPEG,
        CLUSTER_ENCODER_HARDWARE,
        CLUSTER_ENCODER_SOFTWARE,
    ):
        return mode
    return CLUSTER_ENCODER_AUTO


def normalize_cluster_core_mode(value: object) -> int:
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in ("all", "all-cores", "all_cores"):
            return CLUSTER_CORE_MODE_ALL
        if normalized in ("dedicated", "default", "cluster", "1,2,3,4"):
            return CLUSTER_CORE_MODE_DEDICATED
        try:
            value = int(normalized)
        except ValueError:
            return CLUSTER_CORE_MODE_DEDICATED
    try:
        mode = int(value)
    except (TypeError, ValueError):
        return CLUSTER_CORE_MODE_DEDICATED
    if mode == CLUSTER_CORE_MODE_ALL:
        return CLUSTER_CORE_MODE_ALL
    return CLUSTER_CORE_MODE_DEDICATED


def normalize_cluster_priority(value: object) -> int:
    if isinstance(value, str):
        normalized = value.strip()
        try:
            value = int(normalized)
        except ValueError:
            return CLUSTER_PRIORITY_DEFAULT
    try:
        priority = int(value)
    except (TypeError, ValueError):
        return CLUSTER_PRIORITY_DEFAULT
    if priority < CLUSTER_PRIORITY_MIN:
        return CLUSTER_PRIORITY_DEFAULT
    return min(CLUSTER_PRIORITY_MAX, priority)


def normalize_cluster_camera_view_mode(value: object) -> int:
    if isinstance(value, str):
        normalized = value.strip().lower()
        aliases = {
            "default": CLUSTER_CAMERA_VIEW_MODE_DEFAULT,
            "mode0": CLUSTER_CAMERA_VIEW_MODE_DEFAULT,
            "mode-0": CLUSTER_CAMERA_VIEW_MODE_DEFAULT,
            "rear": CLUSTER_CAMERA_VIEW_MODE_EGO_BOTTOM,
            "bottom": CLUSTER_CAMERA_VIEW_MODE_EGO_BOTTOM,
            "ego-bottom": CLUSTER_CAMERA_VIEW_MODE_EGO_BOTTOM,
            "ego_bottom": CLUSTER_CAMERA_VIEW_MODE_EGO_BOTTOM,
            "mode1": CLUSTER_CAMERA_VIEW_MODE_EGO_BOTTOM,
            "mode-1": CLUSTER_CAMERA_VIEW_MODE_EGO_BOTTOM,
            "legacy": CLUSTER_CAMERA_VIEW_MODE_EGO_BOTTOM,
        }
        if normalized in aliases:
            return aliases[normalized]
        try:
            value = int(normalized)
        except ValueError:
            return CLUSTER_CAMERA_VIEW_MODE_DEFAULT
    try:
        mode = int(value)
    except (TypeError, ValueError):
        return CLUSTER_CAMERA_VIEW_MODE_DEFAULT
    if mode == CLUSTER_CAMERA_VIEW_MODE_EGO_BOTTOM:
        return CLUSTER_CAMERA_VIEW_MODE_EGO_BOTTOM
    return CLUSTER_CAMERA_VIEW_MODE_DEFAULT


def normalize_cluster_brightness_percent(value: object) -> int:
    if isinstance(value, str):
        normalized = value.strip()
        try:
            value = int(normalized)
        except ValueError:
            return 0
    try:
        brightness = int(value)
    except (TypeError, ValueError):
        return 0
    return min(100, max(0, brightness))


def normalize_cluster_screen_mode(value: object) -> int:
    if isinstance(value, str):
        normalized = value.strip().lower()
        aliases = {
            "default": CLUSTER_SCREEN_MODE_DEFAULT,
            "debug": CLUSTER_SCREEN_MODE_DEBUG,
            "system": CLUSTER_SCREEN_MODE_DEBUG_SYSTEM,
            "debug-system": CLUSTER_SCREEN_MODE_DEBUG_SYSTEM,
            "debug_system": CLUSTER_SCREEN_MODE_DEBUG_SYSTEM,
            "graph": CLUSTER_SCREEN_MODE_DEBUG_GRAPH,
            "graph-full": CLUSTER_SCREEN_MODE_DEBUG_GRAPH,
            "graph_full": CLUSTER_SCREEN_MODE_DEBUG_GRAPH,
            "graph-right": CLUSTER_SCREEN_MODE_DEBUG_GRAPH_RIGHT,
            "graph_right": CLUSTER_SCREEN_MODE_DEBUG_GRAPH_RIGHT,
            "debug-graph": CLUSTER_SCREEN_MODE_DEBUG_GRAPH,
            "debug_graph": CLUSTER_SCREEN_MODE_DEBUG_GRAPH,
            "debug-graph-right": CLUSTER_SCREEN_MODE_DEBUG_GRAPH_RIGHT,
            "debug_graph_right": CLUSTER_SCREEN_MODE_DEBUG_GRAPH_RIGHT,
            "navi-debug": CLUSTER_SCREEN_MODE_NAVI_DEBUG,
            "navi_debug": CLUSTER_SCREEN_MODE_NAVI_DEBUG,
            "navigation-debug": CLUSTER_SCREEN_MODE_NAVI_DEBUG,
            "navigation_debug": CLUSTER_SCREEN_MODE_NAVI_DEBUG,
        }
        if normalized in aliases:
            return aliases[normalized]
        try:
            value = int(normalized)
        except ValueError:
            return CLUSTER_SCREEN_MODE_DEFAULT
    try:
        mode = int(value)
    except (TypeError, ValueError):
        return CLUSTER_SCREEN_MODE_DEFAULT
    if mode in (
        CLUSTER_SCREEN_MODE_DEFAULT,
        CLUSTER_SCREEN_MODE_DEBUG,
        CLUSTER_SCREEN_MODE_DEBUG_SYSTEM,
        CLUSTER_SCREEN_MODE_DEBUG_GRAPH,
        CLUSTER_SCREEN_MODE_DEBUG_GRAPH_RIGHT,
        CLUSTER_SCREEN_MODE_NAVI_DEBUG,
    ):
        return mode
    return CLUSTER_SCREEN_MODE_DEFAULT


def normalize_cluster_radar_info_mode(value: object) -> int:
    if isinstance(value, str):
        normalized = value.strip().lower()
        aliases = {
            "none": CLUSTER_RADAR_INFO_NONE,
            "off": CLUSTER_RADAR_INFO_NONE,
            "vehicle-speed": CLUSTER_RADAR_INFO_VEHICLE_SPEED,
            "vehicle_speed": CLUSTER_RADAR_INFO_VEHICLE_SPEED,
            "vehicle-speed-distance": CLUSTER_RADAR_INFO_VEHICLE_SPEED_DISTANCE,
            "vehicle_speed_distance": CLUSTER_RADAR_INFO_VEHICLE_SPEED_DISTANCE,
            "all-speed": CLUSTER_RADAR_INFO_ALL_SPEED,
            "all_speed": CLUSTER_RADAR_INFO_ALL_SPEED,
            "all-speed-distance": CLUSTER_RADAR_INFO_ALL_SPEED_DISTANCE,
            "all_speed_distance": CLUSTER_RADAR_INFO_ALL_SPEED_DISTANCE,
        }
        if normalized in aliases:
            return aliases[normalized]
        try:
            value = int(normalized)
        except ValueError:
            return CLUSTER_RADAR_INFO_ALL_SPEED_DISTANCE
    try:
        mode = int(value)
    except (TypeError, ValueError):
        return CLUSTER_RADAR_INFO_ALL_SPEED_DISTANCE
    if CLUSTER_RADAR_INFO_NONE <= mode <= CLUSTER_RADAR_INFO_ALL_SPEED_DISTANCE:
        return mode
    return CLUSTER_RADAR_INFO_ALL_SPEED_DISTANCE


def normalize_cluster_radar_display_mode(value: object) -> int:
    if isinstance(value, str):
        normalized = value.strip().lower()
        aliases = {
            "default": CLUSTER_RADAR_DISPLAY_MERGED,
            "merge": CLUSTER_RADAR_DISPLAY_MERGED,
            "merged": CLUSTER_RADAR_DISPLAY_MERGED,
            "detail": CLUSTER_RADAR_DISPLAY_DETAIL,
            "detailed": CLUSTER_RADAR_DISPLAY_DETAIL,
            "raw": CLUSTER_RADAR_DISPLAY_DETAIL,
        }
        if normalized in aliases:
            return aliases[normalized]
        try:
            value = int(normalized)
        except ValueError:
            return CLUSTER_RADAR_DISPLAY_MERGED
    try:
        mode = int(value)
    except (TypeError, ValueError):
        return CLUSTER_RADAR_DISPLAY_MERGED
    if mode == CLUSTER_RADAR_DISPLAY_DETAIL:
        return CLUSTER_RADAR_DISPLAY_DETAIL
    return CLUSTER_RADAR_DISPLAY_MERGED


def normalize_cluster_radar_source_color_mode(value: object) -> int:
    if isinstance(value, str):
        normalized = value.strip().lower()
        aliases = {
            "default": CLUSTER_RADAR_SOURCE_COLOR_DEFAULT,
            "off": CLUSTER_RADAR_SOURCE_COLOR_DEFAULT,
            "source": CLUSTER_RADAR_SOURCE_COLOR_BY_SOURCE,
            "on": CLUSTER_RADAR_SOURCE_COLOR_BY_SOURCE,
        }
        if normalized in aliases:
            return aliases[normalized]
        try:
            value = int(normalized)
        except ValueError:
            return CLUSTER_RADAR_SOURCE_COLOR_DEFAULT
    try:
        mode = int(value)
    except (TypeError, ValueError):
        return CLUSTER_RADAR_SOURCE_COLOR_DEFAULT
    if mode == CLUSTER_RADAR_SOURCE_COLOR_BY_SOURCE:
        return CLUSTER_RADAR_SOURCE_COLOR_BY_SOURCE
    return CLUSTER_RADAR_SOURCE_COLOR_DEFAULT


def current_cluster_theme(mode: object = "auto", now: float | None = None) -> ClusterTheme:
    normalized = normalize_cluster_theme_mode(mode)
    if normalized == "dark":
        return DARK_CLUSTER_THEME
    if normalized == "light":
        return LIGHT_CLUSTER_THEME

    local_hour = time.localtime(now).tm_hour if now is not None else time.localtime().tm_hour
    if local_hour >= AUTO_DARK_START_HOUR or local_hour < AUTO_LIGHT_START_HOUR:
        return DARK_CLUSTER_THEME
    return LIGHT_CLUSTER_THEME


BG = LIGHT_CLUSTER_THEME.bg
PANEL_BG = LIGHT_CLUSTER_THEME.panel_bg
TEXT = LIGHT_CLUSTER_THEME.text
MUTED = LIGHT_CLUSTER_THEME.muted
FAINT = LIGHT_CLUSTER_THEME.faint
ROAD = LIGHT_CLUSTER_THEME.road
ROAD_EDGE = LIGHT_CLUSTER_THEME.road_edge
WHITE = (255, 255, 255)
BLUE = (38, 132, 255)
BLUE_SOFT = (168, 207, 255)
YELLOW = (218, 202, 37)
GREEN = (20, 188, 104)
AMBER = (244, 172, 54)
RED = (222, 72, 64)
PURPLE = (156, 92, 255)
EGO = (32, 89, 179)
CAR_DARK = LIGHT_CLUSTER_THEME.default_vehicle

MAX_SPEED_KPH = 140.0
MAX_ACCEL_MPS2 = 5.0
CONTROLLER_ACCEL_MPS2 = 3.2
CONTROLLER_BRAKE_MPS2 = 5.0
COAST_DECEL_MPS2 = 0.18
DRAG_DECEL_PER_MPS = 0.012
LANE_CHANGE_SECONDS = 4.2
LANE_CHANGE_MIN_SECONDS = 2.2
LANE_CHANGE_MAX_SECONDS = 4.8
LANE_RECENTER_SECONDS = 1.35
MODEL_DIRECT_LANE_RECENTER_SECONDS = 0.85
DEFAULT_LANE_WIDTH_M = 3.6
MAX_STEERING_ANGLE_DEG = 45.0
TURN_SIGNAL_SECONDS = 5.4
TURN_SIGNAL_BLINK_PERIOD_SECONDS = 1.0
TURN_SIGNAL_BLINK_ON_SECONDS = TURN_SIGNAL_BLINK_PERIOD_SECONDS * 0.5

CAMERA_CENTER_X = 1050.0
CAMERA_HORIZON_Y = 30.0
CAMERA_HEIGHT_M = 1.45
CAMERA_FOCAL_X = 240.0
CAMERA_FOCAL_Y = 1050.0
ROAD_NEAR_M = 0.75
ROAD_FAR_M = 90.0
ROAD_CURVE_M_PER_M2 = 0.0042
EGO_FORWARD_M = 4.18
PATH_START_M = EGO_FORWARD_M
PATH_END_M = 72.0
PATH_HEIGHT_M = 0.10
PATH_LANE_CHANGE_CURVE_START_M = 6.70
PATH_LANE_CHANGE_CURVE_END_M = 15.50
SURROUND_MAX_YAW_DEG = 180.0
SURROUND_MAX_PITCH_DEG = 18.0
SURROUND_VIEW_SMOOTH_SECONDS = 0.16
SURROUND_CAMERA_DISTANCE_M = 6.3
SURROUND_CAMERA_HEIGHT_M = 2.65
SURROUND_TARGET_FORWARD_M = 7.6
SURROUND_TARGET_HEIGHT_M = 0.25
SURROUND_CENTER_Y = 265.0
SURROUND_FOCAL_X = 315.0
SURROUND_FOCAL_Y = 355.0
SURROUND_ROAD_REAR_M = -70.0
SURROUND_ROAD_FRONT_M = 115.0
SURROUND_ROAD_STEPS = 96
SURROUND_ROAD_NEAR_DEPTH_M = 0.75
VEHICLE_WIDTH_M = 1.82
VEHICLE_LENGTH_M = 4.35
VEHICLE_SURROUND_WIDTH_M = 1.05
VEHICLE_SURROUND_LENGTH_M = 1.85
VEHICLE_HEIGHT_M = 1.35
VEHICLE_SURROUND_HEIGHT_MULTIPLIER = 3.0
VEHICLE_LANE_CHANGE_SLOPE = 0.0
VEHICLE_AA_SCALE = 3
VEHICLE_CORNER_RADIUS_PX = 7.5
