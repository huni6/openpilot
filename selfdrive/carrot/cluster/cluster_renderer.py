from __future__ import annotations

from collections import OrderedDict
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from functools import lru_cache
import base64
import math
import os
import time
from pathlib import Path

import pyray as rl

from cluster_config import (
    AMBER,
    BLUE,
    BLUE_SOFT,
    CLUSTER_RADAR_INFO_ALL_SPEED,
    CLUSTER_RADAR_INFO_ALL_SPEED_DISTANCE,
    CLUSTER_RADAR_INFO_NONE,
    CLUSTER_RADAR_INFO_VEHICLE_SPEED,
    CLUSTER_RADAR_INFO_VEHICLE_SPEED_DISTANCE,
    CLUSTER_RADAR_SOURCE_COLOR_BY_SOURCE,
    CLUSTER_SCREEN_MODE_DEBUG,
    CLUSTER_SCREEN_MODE_DEBUG_GRAPH,
    CLUSTER_SCREEN_MODE_DEBUG_GRAPH_RIGHT,
    CLUSTER_SCREEN_MODE_NAVI_DEBUG,
    CLUSTER_SCREEN_MODE_DEBUG_SYSTEM,
    ClusterTheme,
    DESIGN_HEIGHT,
    DESIGN_WIDTH,
    EGO_FORWARD_M,
    GREEN,
    MAX_ACCEL_MPS2,
    MAX_SPEED_KPH,
    RED,
    TEXT,
    WHITE,
    current_cluster_theme,
    normalize_cluster_screen_mode,
    normalize_cluster_theme_mode,
)
from cluster_models import (
    ClusterUiState,
    DebugPlotSnapshot,
    GitBranchStatus,
    LiveDebugInfo,
    NaviDebugInfo,
    NaviGuidanceImage,
    NaviTrafficLightInfo,
    RouteOverlay,
)
from cluster_scene import (
    ClusterScene,
    MeshStrip,
    RADAR_STATIC_OBJECT_SPEED_KPH,
    RadarPointMarker,
    Vec3,
    VehicleBox,
    build_cluster_scene,
)
from cluster_system_monitor import SystemStats, SystemStatsSampler
from cluster_utils import blink_visible, clamp


CLUSTER_DIR = Path(__file__).resolve().parent
SELFDRIVE_DIR = CLUSTER_DIR.parents[1]
OPENPILOT_FONT_DIR = SELFDRIVE_DIR / "assets" / "fonts"
OPENPILOT_ADDON_FONT_DIR = SELFDRIVE_DIR / "assets" / "addon" / "font"
KAIGEN_GOTHIC_KR_BOLD_FONT_PATH = OPENPILOT_FONT_DIR / "KaiGenGothicKR-Bold.ttf"
JETBRAINS_MONO_FONT_PATH = OPENPILOT_FONT_DIR / "JetBrainsMono-Medium.ttf"

# ==========================================
# [수정된 부분] 폴더 충돌 오류 해결 및 EV6/커스텀 아이콘 경로 복구
VEHICLE_MODEL_PATH = CLUSTER_DIR / "assets" / "models" / "ev6" / "ev6_cluster.obj"
FOLLOW_VEHICLE_ICON_PATH = SELFDRIVE_DIR / "assets" / "icons_mici" / "carrot_cruse_gap_trimmed.png"
LFA_ICON_PATH = Path("none.png")
# ==========================================

ACCEL_TEXT_WIDTH_SAMPLES = ("+00.00", "-00.00")
TURN_SIGNAL_LEFT_CENTER_X = 610
TURN_SIGNAL_RIGHT_CENTER_X = 1310
TURN_SIGNAL_CENTER_Y = 72
TURN_SIGNAL_HEAD_HALF_HEIGHT = 38
TURN_SIGNAL_MID_CENTER_X = (TURN_SIGNAL_LEFT_CENTER_X + TURN_SIGNAL_RIGHT_CENTER_X) * 0.5
DRIVE_STATUS_BASE_BOX_SIZE = 46.0
DRIVE_STATUS_ROW_HEIGHT = TURN_SIGNAL_HEAD_HALF_HEIGHT * 2.0
DRIVE_STATUS_SCALE = DRIVE_STATUS_ROW_HEIGHT / DRIVE_STATUS_BASE_BOX_SIZE
GEAR_STATUS_CENTER_X = TURN_SIGNAL_LEFT_CENTER_X + 102
GEAR_STATUS_CENTER_Y = TURN_SIGNAL_CENTER_Y
GEAR_STATUS_BOX_SIZE = DRIVE_STATUS_ROW_HEIGHT * 0.82
GEAR_STATUS_FONT_SIZE = 34.0 * DRIVE_STATUS_SCALE * 0.82
GEAR_STATUS_OUTLINE_WIDTH = 2.0 * DRIVE_STATUS_SCALE
FOLLOW_STATUS_CENTER_X = GEAR_STATUS_CENTER_X + 132
FOLLOW_STATUS_W = 160
FOLLOW_STATUS_H = 42.0 * DRIVE_STATUS_SCALE
FOLLOW_STATUS_GAP_BARS = 4
FOLLOW_GAP_ACTIVE = (187, 61, 145, 255)
FOLLOW_GAP_INACTIVE = (118, 122, 128, 150)
FOLLOW_GAP_BAR_W = 5.4
FOLLOW_GAP_BAR_H = 7.7
FOLLOW_GAP_BAR_R = 1.3
FOLLOW_GAP_BAR_SCALE = 1.75 * DRIVE_STATUS_SCALE
FOLLOW_GAP_BAR_STEP_X = 6.3
FOLLOW_GAP_ICON_ASPECT = 44.0 / 27.5
FOLLOW_GAP_ICON_H = 32.0 * DRIVE_STATUS_SCALE
FOLLOW_GAP_ICON_W = FOLLOW_GAP_ICON_H * FOLLOW_GAP_ICON_ASPECT
TOP_CRUISE_CENTER_X = FOLLOW_STATUS_CENTER_X + 202
TOP_CRUISE_FONT_SIZE = 27.0 * DRIVE_STATUS_SCALE
TOP_CRUISE_UNIT_FONT_SIZE = TOP_CRUISE_FONT_SIZE
LFA_STATUS_CENTER_X = TOP_CRUISE_CENTER_X + 142
LFA_STATUS_ICON_SIZE = 28.0 * DRIVE_STATUS_SCALE
TOP_ICON_SIZE = 34.0 * DRIVE_STATUS_SCALE
DRIVE_STATUS_BOX_RADIUS = 8.0 * DRIVE_STATUS_SCALE
SPEED_VALUE_CENTER_X = 260
SPEED_VALUE_CENTER_Y = 230
SPEED_LIMIT_SIGN_CENTER_X = 460
SPEED_LIMIT_SIGN_CENTER_Y = TURN_SIGNAL_CENTER_Y
SPEED_LIMIT_SIGN_RADIUS = 56.0
SPEED_LIMIT_SOURCE_LABELS = {
    "vehicle": "v",
    "car": "v",
    "v": "v",
    "nav": "n",
    "navigation": "n",
    "n": "n",
    "model": "m",
    "m": "m",
    "vision": "vis",
    "vis": "vis",
    "sim": "sim",
}
SYSTEM_PANEL_X = 1416
SYSTEM_PANEL_Y = 118
SYSTEM_PANEL_W = 476
NAVI_TRAFFIC_PANEL_RIGHT = TURN_SIGNAL_RIGHT_CENTER_X + 96
NAVI_TRAFFIC_PANEL_H = 90
NAVI_TRAFFIC_PANEL_Y = TURN_SIGNAL_CENTER_Y + TURN_SIGNAL_HEAD_HALF_HEIGHT + 10
NAVI_TRAFFIC_SIGNAL_SIZE = 58.0
NAVI_TRAFFIC_SIGNAL_GAP = 10.0
NAVI_TRAFFIC_TEXT_GAP = 14.0
NAVI_TRAFFIC_PANEL_PAD_X = 16.0
NAVI_TRAFFIC_BG_LIGHT = (62, 68, 81)
NAVI_TRAFFIC_BG_DARK = (18, 21, 27)
NAVI_TRAFFIC_BG_OUTLINE = (238, 241, 246)
NAVI_TRAFFIC_OFF_LIGHT = (40, 43, 51)
NAVI_TRAFFIC_OFF_DARK = (36, 39, 47)
NAVI_TRAFFIC_OFF_ARROW = (58, 61, 70)
NAVI_TRAFFIC_RED = (255, 111, 111)
NAVI_TRAFFIC_GREEN = (103, 255, 78)
NAVI_GUIDANCE_IMAGE_X = SYSTEM_PANEL_X + 24
NAVI_GUIDANCE_IMAGE_Y = SYSTEM_PANEL_Y + 210
NAVI_GUIDANCE_IMAGE_W = SYSTEM_PANEL_W - 48
NAVI_GUIDANCE_IMAGE_H = 270
SYSTEM_STATS_REFRESH_SECONDS = 1.0
TEXT_MEASURE_CACHE_LIMIT = 1024
TRIANGLE_STRIP_POINT_CACHE_LIMIT = 256
DEBUG_PLOT_MAX_SAMPLES = 360
DEBUG_PLOT_SAMPLE_SECONDS = 0.05
DEBUG_PLOT_MARGIN = 18.0
DEBUG_PLOT_FULL_X = 500.0
DEBUG_PLOT_FULL_Y = DEBUG_PLOT_MARGIN
DEBUG_PLOT_FULL_W = 1392.0
DEBUG_PLOT_FULL_H = DESIGN_HEIGHT - DEBUG_PLOT_MARGIN * 2.0
DEBUG_PLOT_RIGHT_X = SYSTEM_PANEL_X
DEBUG_PLOT_RIGHT_Y = DEBUG_PLOT_MARGIN
DEBUG_PLOT_RIGHT_W = SYSTEM_PANEL_W
DEBUG_PLOT_RIGHT_H = DESIGN_HEIGHT - DEBUG_PLOT_MARGIN * 2.0
GIT_STATUS_MARGIN = 2
GIT_STATUS_DOT_RADIUS = 7
GIT_STATUS_DOT_TEXT_GAP = 6
GIT_STATUS_MAX_TEXT_W = 610
FPS_STATUS_MARGIN = 4
FPS_STATUS_DOT_RADIUS = 7
FPS_STATUS_DOT_TEXT_GAP = 6
FPS_STATUS_MAX_TEXT_W = 220
CLUSTER_CORE_USAGE_MARGIN = 2
CLUSTER_CORE_USAGE_MAX_TEXT_W = 760
RADAR_LABEL_DISTANCE_FONT_SIZE = 16
RADAR_LABEL_SPEED_FONT_SIZE = 14
VEHICLE_BADGE_DISTANCE_FONT_SIZE = 17
VEHICLE_BADGE_SPEED_FONT_SIZE = 15
RADAR_LABEL_ANCHOR_Z_OFFSET_M = 0.30
VEHICLE_BADGE_ANCHOR_Z_OFFSET_M = 0.32
WORLD_LABEL_NEAR_M = 18.0
WORLD_LABEL_FAR_M = 180.0
WORLD_LABEL_MIN_SCALE = 0.56
WORLD_LABEL_TEXTURE_CACHE_LIMIT = 512
WORLD_LABEL_TEXTURE_SIZE_GRID = 0.25
WORLD_LABEL_TEXTURE_PADDING_PX = 4
VEHICLE_MATERIAL_COLORS: dict[str, tuple[int, int, int, int]] = {
    "body": (156, 166, 172, 255),
    "wheel": (18, 20, 22, 255),
    "besi_roda": (36, 38, 42, 255),
    "light": (184, 222, 255, 255),
    "stop_light": (226, 34, 28, 255),
    "riting": (255, 146, 20, 255),
    "Material": (136, 142, 148, 255),
    "Material.002": (68, 72, 78, 255),
    "Material.003": (18, 20, 22, 255),
    "Material.004": (18, 20, 22, 255),
    "Material.005": (18, 20, 22, 255),
    "Material.006": (18, 20, 22, 255),
}
DEFAULT_VEHICLE_MATERIAL_COLOR = (100, 105, 110, 255)
NV12_PACK_VERTEX_SHADER = """
attribute vec3 vertexPosition;
attribute vec2 vertexTexCoord;
attribute vec4 vertexColor;

varying vec2 fragTexCoord;
varying vec4 fragColor;

uniform mat4 mvp;

void main() {
    fragTexCoord = vertexTexCoord;
    fragColor = vertexColor;
    gl_Position = mvp * vec4(vertexPosition, 1.0);
}
"""
NV12_PACK_FRAGMENT_SHADER = """
#ifdef GL_ES
precision mediump float;
#endif

varying vec2 fragTexCoord;
varying vec4 fragColor;

uniform sampler2D texture0;
uniform vec2 srcSize;
uniform vec2 packedSize;
uniform int plane;
uniform int flipX;

const float Y_PAD = 0.062745;
const float UV_PAD = 0.501961;

vec3 sampleRgb(float x, float y) {
    if (flipX != 0) {
        // The portrait upload transform maps screen horizontal correction to source Y.
        y = srcSize.y - 1.0 - y;
    }
    vec2 clamped = clamp(vec2(x, y), vec2(0.0), srcSize - vec2(1.0));
    return texture2D(texture0, (clamped + vec2(0.5)) / srcSize).rgb;
}

float y601(vec3 rgb) {
    return clamp(0.062745 + 0.256788 * rgb.r + 0.504129 * rgb.g + 0.097906 * rgb.b, 0.0, 1.0);
}

float u601(vec3 rgb) {
    return clamp(0.501961 - 0.148223 * rgb.r - 0.290993 * rgb.g + 0.439216 * rgb.b, 0.0, 1.0);
}

float v601(vec3 rgb) {
    return clamp(0.501961 + 0.439216 * rgb.r - 0.367788 * rgb.g - 0.071427 * rgb.b, 0.0, 1.0);
}

vec3 sample2x2(float x, float y) {
    return (
        sampleRgb(x, y) +
        sampleRgb(x + 1.0, y) +
        sampleRgb(x, y + 1.0) +
        sampleRgb(x + 1.0, y + 1.0)
    ) * 0.25;
}

float packedY(float x, float y) {
    if (x >= srcSize.x || y >= srcSize.y) {
        return Y_PAD;
    }
    return y601(sampleRgb(x, y));
}

vec2 packedUV(float x, float y) {
    if (x >= srcSize.x || y >= srcSize.y) {
        return vec2(UV_PAD, UV_PAD);
    }
    vec3 rgb = sample2x2(x, y);
    return vec2(u601(rgb), v601(rgb));
}

void main() {
    vec2 packedCoord = min(floor(fragTexCoord * packedSize), packedSize - vec2(1.0));
    float baseX = packedCoord.x * 4.0;
    if (plane == 0) {
        float y = packedCoord.y;
        gl_FragColor = vec4(
            packedY(baseX, y),
            packedY(baseX + 1.0, y),
            packedY(baseX + 2.0, y),
            packedY(baseX + 3.0, y)
        );
    } else {
        float y = packedCoord.y * 2.0;
        vec2 left = packedUV(baseX, y);
        vec2 right = packedUV(baseX + 2.0, y);
        gl_FragColor = vec4(left.x, left.y, right.x, right.y);
    }
}
"""


@dataclass(slots=True)
class CachedTextTexture:
    texture: object
    text_width: float
    text_height: float
    texture_width: int
    texture_height: int
    padding_px: float


@lru_cache(maxsize=256)
def _cached_rl_color(r: int, g: int, b: int, a: int) -> rl.Color:
    return rl.Color(r, g, b, a)


def rgba_key(color: tuple[int, int, int] | tuple[int, int, int, int]) -> tuple[int, int, int, int]:
    if len(color) == 4:
        r, g, b, a = color
    else:
        r, g, b = color
        a = 255
    return int(r), int(g), int(b), int(a)


def rl_color(color: tuple[int, int, int] | tuple[int, int, int, int], alpha: int | None = None) -> rl.Color:
    r, g, b, a = rgba_key(color)
    if alpha is not None:
        a = alpha
    return _cached_rl_color(int(r), int(g), int(b), int(a))


def radar_point_distance_label(point: RadarPointMarker) -> str:
    if point.absolute_speed_kph is not None and point.absolute_speed_kph <= RADAR_STATIC_OBJECT_SPEED_KPH:
        return ""
    return f"{point.longitudinal_m:.0f} m"


def radar_point_speed_label(point: RadarPointMarker) -> str:
    if point.absolute_speed_kph is None:
        return ""
    if point.absolute_speed_kph <= RADAR_STATIC_OBJECT_SPEED_KPH:
        return ""
    return f"{point.absolute_speed_kph:.0f} km/h"


def vehicle_distance_label(vehicle: VehicleBox) -> str:
    if vehicle.absolute_speed_kph is not None and vehicle.absolute_speed_kph <= RADAR_STATIC_OBJECT_SPEED_KPH:
        return ""
    return f"{vehicle_distance_m(vehicle):.0f} m"


def vehicle_distance_m(vehicle: VehicleBox) -> float:
    if vehicle.longitudinal_m is not None:
        return vehicle.longitudinal_m
    return vehicle.center.y - EGO_FORWARD_M


def vehicle_speed_label(vehicle: VehicleBox) -> str:
    if vehicle.absolute_speed_kph is None:
        return ""
    if vehicle.absolute_speed_kph <= RADAR_STATIC_OBJECT_SPEED_KPH:
        return ""
    return f"{vehicle.absolute_speed_kph:.0f} km/h"


def radar_info_shows_vehicle(mode: int) -> bool:
    return mode in (
        CLUSTER_RADAR_INFO_VEHICLE_SPEED,
        CLUSTER_RADAR_INFO_VEHICLE_SPEED_DISTANCE,
        CLUSTER_RADAR_INFO_ALL_SPEED,
        CLUSTER_RADAR_INFO_ALL_SPEED_DISTANCE,
    )


def radar_info_shows_radar_points(mode: int) -> bool:
    return mode in (
        CLUSTER_RADAR_INFO_ALL_SPEED,
        CLUSTER_RADAR_INFO_ALL_SPEED_DISTANCE,
    )


def radar_info_shows_speed(mode: int) -> bool:
    return mode != CLUSTER_RADAR_INFO_NONE


def radar_info_shows_distance(mode: int) -> bool:
    return mode in (
        CLUSTER_RADAR_INFO_VEHICLE_SPEED_DISTANCE,
        CLUSTER_RADAR_INFO_ALL_SPEED_DISTANCE,
    )


def vehicle_metric_color(vehicle: VehicleBox, theme: ClusterTheme, source_color_mode: int) -> tuple[int, int, int]:
    if source_color_mode != CLUSTER_RADAR_SOURCE_COLOR_BY_SOURCE:
        return theme.world_label_text
    if vehicle_source_is_adas(vehicle.source):
        return GREEN
    if vehicle_source_is_front_radar(vehicle.source):
        return RED
    if vehicle_source_is_radar_track(vehicle.source):
        return AMBER
    if vehicle_source_is_camera(vehicle.source):
        return BLUE_SOFT
    if vehicle.source.startswith("modelV2"):
        return BLUE
    return theme.world_label_text


def vehicle_source_base(source: str) -> str:
    return source.split("+radar:", 1)[0]


def vehicle_source_is_adas(source: str) -> bool:
    base_source = vehicle_source_base(source)
    return base_source == "carState" or base_source in ("CAN 0x162", "CAN 0x1ea")


def vehicle_source_is_camera(source: str) -> bool:
    return vehicle_source_base(source).startswith("camera")


def vehicle_source_is_front_radar(source: str) -> bool:
    return vehicle_source_base(source) == "radarState"


def vehicle_source_is_radar_track(source: str) -> bool:
    return source in ("radarPoint", "liveTracks") or "+radar:" in source


def speed_limit_source_label(source: str | None) -> str:
    if source is None:
        return ""
    normalized = source.strip().lower()
    if not normalized:
        return ""
    return SPEED_LIMIT_SOURCE_LABELS.get(normalized, normalized[:3])


def world_label_scale(distance_m: float) -> float:
    far_amount = clamp((abs(distance_m) - WORLD_LABEL_NEAR_M) / (WORLD_LABEL_FAR_M - WORLD_LABEL_NEAR_M), 0.0, 1.0)
    return 1.0 - far_amount * (1.0 - WORLD_LABEL_MIN_SCALE)


def vec3(point: Vec3) -> rl.Vector3:
    return rl.Vector3(point.x, point.y, point.z)


def rectangles_overlap(
    left: tuple[float, float, float, float],
    right: tuple[float, float, float, float],
) -> bool:
    lx, ly, lw, lh = left
    rx, ry, rw, rh = right
    return lx < rx + rw and lx + lw > rx and ly < ry + rh and ly + lh > ry


def camera_forward(camera) -> tuple[float, float, float] | None:
    dx = float(camera.target.x - camera.position.x)
    dy = float(camera.target.y - camera.position.y)
    dz = float(camera.target.z - camera.position.z)
    length = math.sqrt(dx * dx + dy * dy + dz * dz)
    if length <= 0.0001 or not all(math.isfinite(value) for value in (dx, dy, dz, length)):
        return None
    return dx / length, dy / length, dz / length


def camera_depth_m(point, camera) -> float | None:
    forward = camera_forward(camera)
    if forward is None:
        return None
    px = float(point.x - camera.position.x)
    py = float(point.y - camera.position.y)
    pz = float(point.z - camera.position.z)
    if not all(math.isfinite(value) for value in (px, py, pz)):
        return None
    fx, fy, fz = forward
    return px * fx + py * fy + pz * fz


def world_to_screen_label_anchor(point, camera, width: int, height: int):
    depth_m = camera_depth_m(point, camera)
    if depth_m is None or depth_m <= 0.05:
        return None
    screen = rl.get_world_to_screen_ex(point, camera, width, height)
    if not math.isfinite(screen.x) or not math.isfinite(screen.y):
        return None
    return screen


def label_rect_inside_bounds(
    rect: tuple[float, float, float, float],
    bounds: tuple[float, float, float, float],
) -> bool:
    x, y, width, height = rect
    left, top, right, bottom = bounds
    values = (x, y, width, height, left, top, right, bottom)
    if not all(math.isfinite(value) for value in values):
        return False
    return x >= left and y >= top and x + width <= right and y + height <= bottom


class ClusterUiRenderer:
    def __init__(
        self,
        width: int = DESIGN_WIDTH,
        height: int = DESIGN_HEIGHT,
        title: str = "carrotpilot cluster",
        target_fps: int = 0,
        theme_mode: str = "auto",
        screen_mode: int = 0,
    ) -> None:
        self.width = width
        self.height = height
        self.title = title
        self.target_fps = target_fps
        self.theme_mode = normalize_cluster_theme_mode(theme_mode)
        self.screen_mode = normalize_cluster_screen_mode(screen_mode)
        self._theme = current_cluster_theme(self.theme_mode)
        self.hidden = False
        self._window_open = False
        self._font = None
        self._owns_font = False
        self._accel_text_width = 0.0
        self._capture_target = None
        self._portrait_upload_target = None
        self._portrait_upload_target_size: tuple[int, int] | None = None
        self._nv12_pack_y_target = None
        self._nv12_pack_y_size: tuple[int, int] | None = None
        self._nv12_pack_uv_target = None
        self._nv12_pack_uv_size: tuple[int, int] | None = None
        self._nv12_pack_full_target = None
        self._nv12_pack_full_size: tuple[int, int] | None = None
        self._nv12_pack_shader = None
        self._nv12_pack_shader_locations: dict[str, int] = {}
        self._vehicle_model = None
        self._vehicle_model_load_attempted = False
        self._follow_vehicle_texture = None
        self._lfa_texture = None
        self._lfa_active_texture = None
        self._navi_guidance_texture = None
        self._navi_guidance_hash = ""
        self._navi_guidance_size: tuple[int, int] | None = None
        self._route_video_texture = None
        self._route_video_size: tuple[int, int] | None = None
        self._route_video_frame_id: str | None = None
        self._left_turn_signal_started_at: float | None = None
        self._right_turn_signal_started_at: float | None = None
        self._triangle_strip_point_cache: OrderedDict[
            tuple[int, int],
            tuple[tuple[Vec3, ...], tuple[Vec3, ...], object, int],
        ] = OrderedDict()
        self._world_label_texture_cache: OrderedDict[
            tuple[int, str, float, float, tuple[int, int, int, int]],
            CachedTextTexture,
        ] = OrderedDict()
        self._world_label_texture_cache_enabled = os.environ.get("CLUSTER_WORLD_LABEL_TEXTURE_CACHE", "0") == "1"
        self._text_measure_cache: dict[tuple[int, str, float, float], tuple[float, float]] = {}
        self._system_stats = SystemStatsSampler(SYSTEM_STATS_REFRESH_SECONDS)
        self._debug_plot_mode_prev = -1
        self._debug_plot_size = 0
        self._debug_plot_index = -1
        self._debug_plot_values = [[0.0] * DEBUG_PLOT_MAX_SAMPLES for _ in range(3)]
        self._debug_plot_min = -2.0
        self._debug_plot_max = 2.0
        self._debug_plot_last_sample_time: float | None = None
        self.profile_enabled = os.environ.get("CLUSTER_PROFILE_RENDER") == "1"
        self._profile_samples: list[tuple[str, float]] = []

    def set_profile_enabled(self, enabled: bool) -> None:
        self.profile_enabled = enabled

    def set_theme_mode(self, theme_mode: str) -> None:
        self.theme_mode = normalize_cluster_theme_mode(theme_mode)
        self._theme = current_cluster_theme(self.theme_mode)

    def set_screen_mode(self, screen_mode: int) -> None:
        self.screen_mode = normalize_cluster_screen_mode(screen_mode)

    def set_target_fps(self, target_fps: int) -> None:
        self.target_fps = max(0, int(target_fps))
        if self._window_open:
            profile_stage = self._profile_start()
            rl.set_target_fps(self.target_fps)
            self._profile_add("renderer.set_target_fps", profile_stage)

    def _current_theme(self) -> ClusterTheme:
        self._theme = current_cluster_theme(self.theme_mode)
        return self._theme

    def clear_profile_samples(self) -> None:
        self._profile_samples.clear()

    def profile_samples(self) -> list[tuple[str, float]]:
        return self._profile_samples

    def _profile_start(self) -> float:
        return time.perf_counter() if self.profile_enabled else 0.0

    def _profile_add(self, name: str, start_time: float) -> None:
        if self.profile_enabled:
            self._profile_samples.append((name, (time.perf_counter() - start_time) * 1000.0))

    def _profile_add_elapsed(self, name: str, elapsed_ms: float) -> None:
        if self.profile_enabled:
            self._profile_samples.append((name, elapsed_ms))

    def open(self, hidden: bool = False) -> None:
        if self._window_open:
            return
        profile_total = self._profile_start()
        self.hidden = hidden
        rl.set_trace_log_level(rl.TraceLogLevel.LOG_WARNING)
        rl.set_config_flags(rl.ConfigFlags.FLAG_MSAA_4X_HINT)
        flags = 0
        if hidden:
            flags |= rl.ConfigFlags.FLAG_WINDOW_HIDDEN
        if flags:
            rl.set_config_flags(flags)
        profile_stage = self._profile_start()
        rl.init_window(self.width, self.height, self.title)
        self._profile_add("renderer.open.init_window", profile_stage)
        if self.target_fps > 0:
            profile_stage = self._profile_start()
            rl.set_target_fps(self.target_fps)
            self._profile_add("renderer.open.set_target_fps", profile_stage)
        profile_stage = self._profile_start()
        self._font = self._load_font()
        self._profile_add("renderer.open.load_font", profile_stage)
        profile_stage = self._profile_start()
        self._load_vehicle_model()
        self._profile_add("renderer.open.load_vehicle_model", profile_stage)
        profile_stage = self._profile_start()
        self._load_follow_vehicle_texture()
        self._profile_add("renderer.open.load_follow_vehicle_texture", profile_stage)
        profile_stage = self._profile_start()
        self._load_drive_status_textures()
        self._profile_add("renderer.open.load_drive_status_textures", profile_stage)
        self._window_open = True
        self._profile_add("renderer.open.total", profile_total)

    def close(self) -> None:
        if not self._window_open:
            return
        if self._capture_target is not None:
            rl.unload_render_texture(self._capture_target)
            self._capture_target = None
        if self._portrait_upload_target is not None:
            rl.unload_render_texture(self._portrait_upload_target)
            self._portrait_upload_target = None
            self._portrait_upload_target_size = None
        if self._nv12_pack_y_target is not None:
            rl.unload_render_texture(self._nv12_pack_y_target)
            self._nv12_pack_y_target = None
            self._nv12_pack_y_size = None
        if self._nv12_pack_uv_target is not None:
            rl.unload_render_texture(self._nv12_pack_uv_target)
            self._nv12_pack_uv_target = None
            self._nv12_pack_uv_size = None
        if self._nv12_pack_full_target is not None:
            rl.unload_render_texture(self._nv12_pack_full_target)
            self._nv12_pack_full_target = None
            self._nv12_pack_full_size = None
        if self._nv12_pack_shader is not None:
            rl.unload_shader(self._nv12_pack_shader)
            self._nv12_pack_shader = None
            self._nv12_pack_shader_locations = {}
        for cached_text in self._world_label_texture_cache.values():
            rl.unload_texture(cached_text.texture)
        self._world_label_texture_cache.clear()
        if self._route_video_texture is not None:
            rl.unload_texture(self._route_video_texture)
            self._route_video_texture = None
        if self._follow_vehicle_texture is not None:
            rl.unload_texture(self._follow_vehicle_texture)
            self._follow_vehicle_texture = None
        if self._lfa_texture is not None:
            rl.unload_texture(self._lfa_texture)
            self._lfa_texture = None
        if self._lfa_active_texture is not None:
            rl.unload_texture(self._lfa_active_texture)
            self._lfa_active_texture = None
        if self._navi_guidance_texture is not None:
            rl.unload_texture(self._navi_guidance_texture)
            self._navi_guidance_texture = None
            self._navi_guidance_hash = ""
            self._navi_guidance_size = None
        if self._owns_font and self._font is not None:
            rl.unload_font(self._font)
        self._font = None
        self._owns_font = False
        self._accel_text_width = 0.0
        if self._vehicle_model is not None:
            rl.unload_model(self._vehicle_model)
            self._vehicle_model = None
        self._vehicle_model_load_attempted = False
        self._route_video_size = None
        self._route_video_frame_id = None
        rl.close_window()
        self._window_open = False

    def should_close(self) -> bool:
        return bool(self._window_open and rl.window_should_close())

    def render_frame(self, state: ClusterUiState) -> None:
        self.open()
        profile_stage = self._profile_start()
        rl.begin_drawing()
        self._profile_add("render_frame.begin_drawing", profile_stage)
        profile_stage = self._profile_start()
        self.render(state)
        self._profile_add("render_frame.render", profile_stage)
        profile_stage = self._profile_start()
        rl.end_drawing()
        self._profile_add("render_frame.end_drawing", profile_stage)

    def render(self, state: ClusterUiState, signal_lights: tuple[bool, bool] | None = None) -> None:
        """Draw one frame into the currently active raylib render target."""
        if signal_lights is None:
            signal_lights = self._turn_signal_lights(state)
        profile_stage = self._profile_start()
        if self.screen_mode == CLUSTER_SCREEN_MODE_DEBUG_GRAPH:
            self._clear_world()
        else:
            self._render_world(state, signal_lights)
        self._profile_add("render.world", profile_stage)
        profile_stage = self._profile_start()
        self._draw_hud(state, signal_lights)
        self._profile_add("render.hud", profile_stage)

    def _clear_world(self) -> None:
        theme = self._current_theme()
        profile_stage = self._profile_start()
        rl.clear_background(rl_color(theme.bg))
        self._profile_add("render_world.clear_background", profile_stage)

    def _render_world(self, state: ClusterUiState, signal_lights: tuple[bool, bool] | None = None) -> None:
        if signal_lights is None:
            signal_lights = self._turn_signal_lights(state)
        theme = self._current_theme()
        profile_stage = self._profile_start()
        scene = build_cluster_scene(
            state,
            self._profile_add_elapsed if self.profile_enabled else None,
            highlight_lane_lit=self._highlight_lane_lit(state, signal_lights),
            theme=theme,
        )
        self._profile_add("render_world.build_scene", profile_stage)
        profile_stage = self._profile_start()
        rl.clear_background(rl_color(theme.bg))
        self._profile_add("render_world.clear_background", profile_stage)
        profile_stage = self._profile_start()
        self._draw_scene(scene, state)
        self._profile_add("render_world.draw_scene", profile_stage)

    def render_to_file(self, state: ClusterUiState, output_path: str | Path) -> None:
        image = self._render_to_image(state)
        try:
            rl.export_image(image, str(output_path))
        finally:
            rl.unload_image(image)

    def render_to_png_bytes(self, state: ClusterUiState, portrait_upload: bool = False) -> bytes:
        profile_stage = self._profile_start()
        image = self._render_to_image(state, portrait_upload=portrait_upload)
        self._profile_add("render_to_png.render_to_image", profile_stage)
        try:
            size = rl.ffi.new("int *")
            profile_stage = self._profile_start()
            data = rl.export_image_to_memory(image, ".png", size)
            self._profile_add("render_to_png.export_png", profile_stage)
            try:
                if size[0] <= 0:
                    raise RuntimeError("raylib failed to encode frame as PNG")
                return bytes(rl.ffi.buffer(data, size[0]))
            finally:
                rl.mem_free(data)
        finally:
            profile_stage = self._profile_start()
            rl.unload_image(image)
            self._profile_add("render_to_png.unload_image", profile_stage)

    def render_to_rgba_bytes(
        self,
        state: ClusterUiState,
        portrait_upload: bool = False,
        output_width: int | None = None,
        output_height: int | None = None,
    ) -> tuple[bytes, int, int]:
        with self.render_to_rgba_buffer(
            state,
            portrait_upload=portrait_upload,
            output_width=output_width,
            output_height=output_height,
        ) as (
            rgba_buffer,
            image_width,
            image_height,
        ):
            profile_stage = self._profile_start()
            rgba = bytes(rgba_buffer)
            self._profile_add("render_to_rgba.copy_bytes", profile_stage)
            return rgba, image_width, image_height

    @contextmanager
    def render_to_rgba_buffer(
        self,
        state: ClusterUiState,
        portrait_upload: bool = False,
        output_width: int | None = None,
        output_height: int | None = None,
    ) -> Iterator[tuple[object, int, int]]:
        profile_stage = self._profile_start()
        image = self._render_to_image(
            state,
            portrait_upload=portrait_upload,
            output_width=output_width,
            output_height=output_height,
        )
        self._profile_add("render_to_rgba.render_to_image", profile_stage)

        try:
            if image.format != rl.PixelFormat.PIXELFORMAT_UNCOMPRESSED_R8G8B8A8:
                profile_stage = self._profile_start()
                rl.image_format(image, rl.PixelFormat.PIXELFORMAT_UNCOMPRESSED_R8G8B8A8)
                self._profile_add("render_to_rgba.image_format", profile_stage)

            byte_count = image.width * image.height * 4
            profile_stage = self._profile_start()
            rgba_buffer = rl.ffi.buffer(image.data, byte_count)
            self._profile_add("render_to_rgba.buffer_view", profile_stage)
            yield rgba_buffer, image.width, image.height
        finally:
            profile_stage = self._profile_start()
            rl.unload_image(image)
            self._profile_add("render_to_rgba.unload_image", profile_stage)

    @contextmanager
    def render_to_nv12_buffer(
        self,
        state: ClusterUiState,
        output_width: int,
        output_height: int,
        stride: int,
        y_scanlines: int,
        uv_scanlines: int,
        uv_offset: int,
        byte_count: int,
        buffer: bytearray | None = None,
        flip_x: bool = False,
    ) -> Iterator[object]:
        self.open(hidden=self.hidden)
        output_width = int(output_width)
        output_height = int(output_height)
        stride = int(stride)
        y_scanlines = int(y_scanlines)
        uv_scanlines = int(uv_scanlines)
        uv_offset = int(uv_offset)
        byte_count = int(byte_count)
        if output_width <= 0 or output_height <= 0 or stride <= 0 or byte_count <= 0:
            raise RuntimeError("NV12 render target layout is invalid")
        if stride < output_width or y_scanlines < output_height or uv_scanlines < (output_height + 1) // 2:
            raise RuntimeError("NV12 render target layout is smaller than the rendered frame")
        if uv_offset < stride * y_scanlines or byte_count < uv_offset + stride * uv_scanlines:
            raise RuntimeError("NV12 render target byte layout is inconsistent")

        profile_stage = self._profile_start()
        target = self._get_capture_target()
        self._profile_add("render_to_nv12.get_capture_target", profile_stage)

        profile_stage = self._profile_start()
        rl.begin_texture_mode(target)
        self.render(state)
        rl.end_texture_mode()
        self._profile_add("render_to_nv12.draw_to_target", profile_stage)

        profile_stage = self._profile_start()
        upload_target = self._get_portrait_upload_target(output_width, output_height)
        self._profile_add("render_to_nv12.get_portrait_upload_target", profile_stage)

        profile_stage = self._profile_start()
        rl.begin_texture_mode(upload_target)
        rl.clear_background(rl_color(self._current_theme().bg))
        source = rl.Rectangle(
            0.0,
            0.0,
            float(target.texture.width),
            float(target.texture.height),
        )
        dest = rl.Rectangle(
            0.0,
            float(self.width),
            float(self.width),
            float(self.height),
        )
        rl.draw_texture_pro(
            target.texture,
            source,
            dest,
            rl.Vector2(0.0, 0.0),
            -90.0,
            rl_color(WHITE),
        )
        rl.end_texture_mode()
        self._profile_add("render_to_nv12.gpu_upload_transform", profile_stage)

        pack_direct_input = stride % 4 == 0 and byte_count % stride == 0 and uv_offset % stride == 0
        if pack_direct_input:
            full_pack_w = stride // 4
            full_pack_h = byte_count // stride
            tail_pack_h = max(0, full_pack_h - y_scanlines - uv_scanlines)
            uv_pack_y = tail_pack_h
            y_pack_y = tail_pack_h + uv_scanlines

            profile_stage = self._profile_start()
            full_target = self._get_nv12_pack_target("full", full_pack_w, full_pack_h)
            self._profile_add("render_to_nv12.get_pack_targets", profile_stage)

            profile_stage = self._profile_start()
            self._render_nv12_pack_plane(
                upload_target.texture,
                full_target,
                output_width,
                output_height,
                0,
                flip_x,
                packed_width=full_pack_w,
                packed_height=y_scanlines,
                dest_y=y_pack_y,
                clear_target=True,
                clear_color=(128, 128, 128, 128),
            )
            self._profile_add("render_to_nv12.pack_y_shader", profile_stage)

            profile_stage = self._profile_start()
            self._render_nv12_pack_plane(
                upload_target.texture,
                full_target,
                output_width,
                output_height,
                1,
                flip_x,
                packed_width=full_pack_w,
                packed_height=uv_scanlines,
                dest_y=uv_pack_y,
                clear_target=False,
            )
            self._profile_add("render_to_nv12.pack_uv_shader", profile_stage)

            profile_stage = self._profile_start()
            image = rl.load_image_from_texture(full_target.texture)
            self._profile_add("render_to_nv12.readback_packed", profile_stage)

            try:
                if image.format != rl.PixelFormat.PIXELFORMAT_UNCOMPRESSED_R8G8B8A8:
                    profile_stage = self._profile_start()
                    rl.image_format(image, rl.PixelFormat.PIXELFORMAT_UNCOMPRESSED_R8G8B8A8)
                    self._profile_add("render_to_nv12.packed_image_format", profile_stage)

                profile_stage = self._profile_start()
                nv12_buffer = rl.ffi.buffer(image.data, byte_count)
                self._profile_add("render_to_nv12.buffer_view", profile_stage)
                yield nv12_buffer
            finally:
                profile_stage = self._profile_start()
                rl.unload_image(image)
                self._profile_add("render_to_nv12.unload_image", profile_stage)
            return

        pack_full_stride = stride % 4 == 0
        if pack_full_stride:
            y_pack_w = stride // 4
            y_pack_h = y_scanlines
            uv_pack_w = stride // 4
            uv_pack_h = uv_scanlines
        else:
            y_pack_w = (output_width + 3) // 4
            y_pack_h = output_height
            uv_pack_w = (output_width + 3) // 4
            uv_pack_h = (output_height + 1) // 2
        profile_stage = self._profile_start()
        y_target = self._get_nv12_pack_target("y", y_pack_w, y_pack_h)
        uv_target = self._get_nv12_pack_target("uv", uv_pack_w, uv_pack_h)
        self._profile_add("render_to_nv12.get_pack_targets", profile_stage)

        profile_stage = self._profile_start()
        self._render_nv12_pack_plane(upload_target.texture, y_target, output_width, output_height, 0, flip_x)
        self._profile_add("render_to_nv12.pack_y_shader", profile_stage)

        profile_stage = self._profile_start()
        y_image = rl.load_image_from_texture(y_target.texture)
        self._profile_add("render_to_nv12.readback_y", profile_stage)

        profile_stage = self._profile_start()
        self._render_nv12_pack_plane(upload_target.texture, uv_target, output_width, output_height, 1, flip_x)
        self._profile_add("render_to_nv12.pack_uv_shader", profile_stage)

        profile_stage = self._profile_start()
        uv_image = rl.load_image_from_texture(uv_target.texture)
        self._profile_add("render_to_nv12.readback_uv", profile_stage)

        try:
            if y_image.format != rl.PixelFormat.PIXELFORMAT_UNCOMPRESSED_R8G8B8A8:
                profile_stage = self._profile_start()
                rl.image_format(y_image, rl.PixelFormat.PIXELFORMAT_UNCOMPRESSED_R8G8B8A8)
                self._profile_add("render_to_nv12.y_image_format", profile_stage)
            if uv_image.format != rl.PixelFormat.PIXELFORMAT_UNCOMPRESSED_R8G8B8A8:
                profile_stage = self._profile_start()
                rl.image_format(uv_image, rl.PixelFormat.PIXELFORMAT_UNCOMPRESSED_R8G8B8A8)
                self._profile_add("render_to_nv12.uv_image_format", profile_stage)

            if buffer is None or len(buffer) != byte_count:
                buffer = bytearray(byte_count)
                buffer[:min(uv_offset, byte_count)] = b"\x10" * min(uv_offset, byte_count)
                if uv_offset < byte_count:
                    buffer[uv_offset:] = b"\x80" * (byte_count - uv_offset)

            y_row_bytes = y_pack_w * 4
            uv_row_bytes = uv_pack_w * 4
            y_data = rl.ffi.buffer(y_image.data, y_row_bytes * y_pack_h)
            uv_data = rl.ffi.buffer(uv_image.data, uv_row_bytes * uv_pack_h)

            if pack_full_stride:
                y_plane_bytes = stride * y_scanlines
                uv_plane_bytes = stride * uv_scanlines
                profile_stage = self._profile_start()
                buffer[:y_plane_bytes] = y_data[:y_plane_bytes]
                self._profile_add("render_to_nv12.copy_y", profile_stage)

                profile_stage = self._profile_start()
                buffer[uv_offset:uv_offset + uv_plane_bytes] = uv_data[:uv_plane_bytes]
                self._profile_add("render_to_nv12.copy_uv", profile_stage)
            else:
                profile_stage = self._profile_start()
                for row in range(output_height):
                    src_start = row * y_row_bytes
                    dst_start = row * stride
                    buffer[dst_start:dst_start + output_width] = y_data[src_start:src_start + output_width]
                self._profile_add("render_to_nv12.copy_y", profile_stage)

                profile_stage = self._profile_start()
                for row in range(uv_pack_h):
                    src_start = row * uv_row_bytes
                    dst_start = uv_offset + row * stride
                    buffer[dst_start:dst_start + output_width] = uv_data[src_start:src_start + output_width]
                self._profile_add("render_to_nv12.copy_uv", profile_stage)
            yield buffer
        finally:
            profile_stage = self._profile_start()
            rl.unload_image(y_image)
            rl.unload_image(uv_image)
            self._profile_add("render_to_nv12.unload_images", profile_stage)

    def _render_to_image(
        self,
        state: ClusterUiState,
        portrait_upload: bool = False,
        output_width: int | None = None,
        output_height: int | None = None,
    ):
        self.open(hidden=self.hidden)
        profile_stage = self._profile_start()
        target = self._get_capture_target()
        self._profile_add("render_to_image.get_capture_target", profile_stage)

        profile_stage = self._profile_start()
        rl.begin_texture_mode(target)
        self.render(state)
        rl.end_texture_mode()
        self._profile_add("render_to_image.draw_to_target", profile_stage)

        if portrait_upload:
            profile_stage = self._profile_start()
            upload_target = self._get_portrait_upload_target(output_width, output_height)
            self._profile_add("render_to_image.get_portrait_upload_target", profile_stage)

            profile_stage = self._profile_start()
            rl.begin_texture_mode(upload_target)
            rl.clear_background(rl_color(self._current_theme().bg))
            source = rl.Rectangle(
                0.0,
                0.0,
                float(target.texture.width),
                float(target.texture.height),
            )
            dest = rl.Rectangle(
                0.0,
                float(self.width),
                float(self.width),
                float(self.height),
            )
            origin = rl.Vector2(0.0, 0.0)
            rl.draw_texture_pro(
                target.texture,
                source,
                dest,
                origin,
                -90.0,
                rl_color(WHITE),
            )
            rl.end_texture_mode()
            self._profile_add("render_to_image.gpu_upload_transform", profile_stage)

            profile_stage = self._profile_start()
            image = rl.load_image_from_texture(upload_target.texture)
            self._profile_add("render_to_image.readback_upload_texture", profile_stage)
        else:
            profile_stage = self._profile_start()
            image = rl.load_image_from_texture(target.texture)
            self._profile_add("render_to_image.readback_texture", profile_stage)

            profile_stage = self._profile_start()
            rl.image_flip_vertical(image)
            self._profile_add("render_to_image.flip_vertical", profile_stage)

        return image

    def _get_capture_target(self):
        if self._capture_target is None:
            profile_stage = self._profile_start()
            self._capture_target = rl.load_render_texture(self.width, self.height)
            self._profile_add("render_target.alloc_capture", profile_stage)
            profile_stage = self._profile_start()
            rl.set_texture_filter(self._capture_target.texture, rl.TextureFilter.TEXTURE_FILTER_BILINEAR)
            self._profile_add("render_target.filter_capture", profile_stage)
        return self._capture_target

    def _get_portrait_upload_target(self, width: int | None = None, height: int | None = None):
        target_width = int(width or self.height)
        target_height = int(height or self.width)
        target_size = (target_width, target_height)
        if self._portrait_upload_target is not None and self._portrait_upload_target_size != target_size:
            rl.unload_render_texture(self._portrait_upload_target)
            self._portrait_upload_target = None
            self._portrait_upload_target_size = None
        if self._portrait_upload_target is None:
            profile_stage = self._profile_start()
            self._portrait_upload_target = rl.load_render_texture(target_width, target_height)
            self._portrait_upload_target_size = target_size
            self._profile_add("render_target.alloc_portrait_upload", profile_stage)
            profile_stage = self._profile_start()
            rl.set_texture_filter(self._portrait_upload_target.texture, rl.TextureFilter.TEXTURE_FILTER_BILINEAR)
            self._profile_add("render_target.filter_portrait_upload", profile_stage)
        return self._portrait_upload_target

    def _get_nv12_pack_target(self, plane: str, width: int, height: int):
        target_size = (int(width), int(height))
        if plane == "y":
            current = self._nv12_pack_y_target
            current_size = self._nv12_pack_y_size
        elif plane == "uv":
            current = self._nv12_pack_uv_target
            current_size = self._nv12_pack_uv_size
        elif plane == "full":
            current = self._nv12_pack_full_target
            current_size = self._nv12_pack_full_size
        else:
            raise RuntimeError(f"unknown NV12 pack plane: {plane}")

        if current is not None and current_size != target_size:
            rl.unload_render_texture(current)
            current = None
            current_size = None
        if current is None:
            profile_stage = self._profile_start()
            current = rl.load_render_texture(target_size[0], target_size[1])
            self._profile_add(f"render_target.alloc_nv12_{plane}", profile_stage)
            profile_stage = self._profile_start()
            rl.set_texture_filter(current.texture, rl.TextureFilter.TEXTURE_FILTER_POINT)
            self._profile_add(f"render_target.filter_nv12_{plane}", profile_stage)
            current_size = target_size

        if plane == "y":
            self._nv12_pack_y_target = current
            self._nv12_pack_y_size = current_size
        elif plane == "uv":
            self._nv12_pack_uv_target = current
            self._nv12_pack_uv_size = current_size
        else:
            self._nv12_pack_full_target = current
            self._nv12_pack_full_size = current_size
        return current

    def _get_nv12_pack_shader(self):
        if self._nv12_pack_shader is None:
            profile_stage = self._profile_start()
            self._nv12_pack_shader = rl.load_shader_from_memory(NV12_PACK_VERTEX_SHADER, NV12_PACK_FRAGMENT_SHADER)
            self._profile_add("render_to_nv12.load_pack_shader", profile_stage)
            if not rl.is_shader_valid(self._nv12_pack_shader):
                raise RuntimeError("failed to load NV12 pack shader")
            self._nv12_pack_shader_locations = {
                "srcSize": rl.get_shader_location(self._nv12_pack_shader, "srcSize"),
                "packedSize": rl.get_shader_location(self._nv12_pack_shader, "packedSize"),
                "plane": rl.get_shader_location(self._nv12_pack_shader, "plane"),
                "flipX": rl.get_shader_location(self._nv12_pack_shader, "flipX"),
            }
        return self._nv12_pack_shader

    def _render_nv12_pack_plane(
        self,
        source_texture,
        target,
        source_width: int,
        source_height: int,
        plane: int,
        flip_x: bool,
        packed_width: int | None = None,
        packed_height: int | None = None,
        dest_y: int = 0,
        clear_target: bool = True,
        clear_color: tuple[int, int, int, int] = (0, 0, 0, 0),
    ) -> None:
        shader = self._get_nv12_pack_shader()
        locations = self._nv12_pack_shader_locations
        pack_width = int(packed_width) if packed_width is not None else int(target.texture.width)
        pack_height = int(packed_height) if packed_height is not None else int(target.texture.height)
        src_size = rl.ffi.new("float[]", [float(source_width), float(source_height)])
        packed_size = rl.ffi.new("float[]", [float(pack_width), float(pack_height)])
        plane_value = rl.ffi.new("int[]", [int(plane)])
        flip_x_value = rl.ffi.new("int[]", [1 if flip_x else 0])
        rl.set_shader_value(shader, locations["srcSize"], src_size, rl.ShaderUniformDataType.SHADER_UNIFORM_VEC2)
        rl.set_shader_value(shader, locations["packedSize"], packed_size, rl.ShaderUniformDataType.SHADER_UNIFORM_VEC2)
        rl.set_shader_value(shader, locations["plane"], plane_value, rl.ShaderUniformDataType.SHADER_UNIFORM_INT)
        rl.set_shader_value(shader, locations["flipX"], flip_x_value, rl.ShaderUniformDataType.SHADER_UNIFORM_INT)

        rl.begin_texture_mode(target)
        if clear_target:
            rl.clear_background(rl_color(clear_color))
        rl.begin_shader_mode(shader)
        rl.rl_set_blend_factors(rl.RL_ONE, rl.RL_ZERO, rl.RL_FUNC_ADD)
        rl.begin_blend_mode(rl.BlendMode.BLEND_CUSTOM)
        try:
            rl.draw_texture_pro(
                source_texture,
                rl.Rectangle(0.0, 0.0, float(source_width), float(source_height)),
                rl.Rectangle(0.0, float(dest_y), float(pack_width), float(pack_height)),
                rl.Vector2(0.0, 0.0),
                0.0,
                rl_color(WHITE),
            )
        finally:
            rl.end_blend_mode()
            rl.end_shader_mode()
            rl.end_texture_mode()

    def _load_font(self):
        for candidate in self._font_candidates():
            if candidate.exists():
                try:
                    font = rl.load_font_ex(str(candidate), 160, None, 0)
                    if font.texture.id > 0:
                        rl.gen_texture_mipmaps(font.texture)
                        rl.set_texture_filter(font.texture, rl.TextureFilter.TEXTURE_FILTER_TRILINEAR)
                        self._owns_font = True
                        return font
                except Exception as exc:
                    print(f"Cluster font load failed for {candidate}: {exc}")
        self._owns_font = False
        return rl.get_font_default()

    def _font_candidates(self) -> list[Path]:
        return [
            KAIGEN_GOTHIC_KR_BOLD_FONT_PATH,
            OPENPILOT_ADDON_FONT_DIR / "KaiGenGothicKR-Bold.ttf",
            JETBRAINS_MONO_FONT_PATH,
            OPENPILOT_FONT_DIR / "JetBrainsMono-Bold.ttf",
            Path("/data/openpilot/selfdrive/assets/fonts/KaiGenGothicKR-Bold.ttf"),
            Path("/data/openpilot/selfdrive/assets/addon/font/KaiGenGothicKR-Bold.ttf"),
            Path("/usr/share/fonts/truetype/jetbrains-mono/JetBrainsMono-Medium.ttf"),
            Path("/usr/share/fonts/TTF/JetBrainsMono-Medium.ttf"),
            Path("/usr/local/share/fonts/JetBrainsMono-Medium.ttf"),
        ]

    def _load_vehicle_model(self) -> None:
        if self._vehicle_model_load_attempted:
            return
        self._vehicle_model_load_attempted = True
        if not VEHICLE_MODEL_PATH.exists():
            return
        try:
            profile_stage = self._profile_start()
            mesh = self._load_obj_mesh(VEHICLE_MODEL_PATH)
            self._profile_add("vehicle_model.parse_obj", profile_stage)
            profile_stage = self._profile_start()
            rl.upload_mesh(rl.ffi.addressof(mesh), False)
            self._profile_add("vehicle_model.upload_mesh", profile_stage)
            profile_stage = self._profile_start()
            model = rl.load_model_from_mesh(mesh)
            self._profile_add("vehicle_model.load_from_mesh", profile_stage)
            if not rl.is_model_valid(model):
                rl.unload_model(model)
                return
            self._vehicle_model = model
        except Exception as exc:
            print(f"Cybertruck vehicle model load failed: {exc}")
            self._vehicle_model = None

    def _load_follow_vehicle_texture(self) -> None:
        if self._follow_vehicle_texture is not None:
            return
        self._follow_vehicle_texture = self._load_icon_texture(FOLLOW_VEHICLE_ICON_PATH, "Follow gap vehicle")

    def _load_drive_status_textures(self) -> None:
        if self._lfa_texture is None:
            self._lfa_texture = self._load_icon_texture(LFA_ICON_PATH, "LFA")
        if self._lfa_active_texture is None:
            self._lfa_active_texture = self._load_lfa_active_texture()

    def _load_icon_texture(self, path: Path, label: str):
        if not path.exists():
            return None
        try:
            texture = rl.load_texture(str(path))
            if texture.id <= 0:
                return None
            rl.set_texture_filter(texture, rl.TextureFilter.TEXTURE_FILTER_BILINEAR)
            return texture
        except Exception as exc:
            print(f"{label} icon load failed: {exc}")
            return None

    def _load_lfa_active_texture(self):
        if not LFA_ICON_PATH.exists():
            return None
        image = None
        try:
            image = rl.load_image(str(LFA_ICON_PATH))
            if not rl.is_image_valid(image):
                return None
            if image.format != rl.PixelFormat.PIXELFORMAT_UNCOMPRESSED_R8G8B8A8:
                rl.image_format(image, rl.PixelFormat.PIXELFORMAT_UNCOMPRESSED_R8G8B8A8)

            data = rl.ffi.cast("unsigned char *", image.data)
            byte_count = image.width * image.height * 4
            green_r, green_g, green_b = GREEN
            for offset in range(0, byte_count, 4):
                alpha = int(data[offset + 3])
                if alpha == 0:
                    continue
                red = int(data[offset])
                green = int(data[offset + 1])
                blue = int(data[offset + 2])
                if red >= 220 and green >= 220 and blue >= 220:
                    data[offset] = green_r
                    data[offset + 1] = green_g
                    data[offset + 2] = green_b

            texture = rl.load_texture_from_image(image)
            if texture.id <= 0:
                return None
            rl.set_texture_filter(texture, rl.TextureFilter.TEXTURE_FILTER_BILINEAR)
            return texture
        except Exception as exc:
            print(f"LFA active icon load failed: {exc}")
            return None
        finally:
            if image is not None and rl.is_image_valid(image):
                rl.unload_image(image)

    def _load_obj_mesh(self, path: Path):
        vertices: list[tuple[float, float, float]] = []
        normals: list[tuple[float, float, float]] = []
        mesh_vertices: list[float] = []
        mesh_normals: list[float] = []
        mesh_colors: list[int] = []
        material_color = DEFAULT_VEHICLE_MATERIAL_COLOR

        def resolve_index(index_text: str, count: int) -> int:
            index = int(index_text)
            if index < 0:
                index = count + index + 1
            return index - 1

        def parse_face_token(token: str) -> tuple[int, int | None]:
            parts = token.split("/")
            vertex_index = resolve_index(parts[0], len(vertices))
            normal_index = None
            if len(parts) >= 3 and parts[2]:
                normal_index = resolve_index(parts[2], len(normals))
            return vertex_index, normal_index

        def face_normal(points: tuple[tuple[float, float, float], ...]) -> tuple[float, float, float]:
            ax, ay, az = points[0]
            bx, by, bz = points[1]
            cx, cy, cz = points[2]
            ux, uy, uz = bx - ax, by - ay, bz - az
            vx, vy, vz = cx - ax, cy - ay, cz - az
            nx = uy * vz - uz * vy
            ny = uz * vx - ux * vz
            nz = ux * vy - uy * vx
            length = math.sqrt(nx * nx + ny * ny + nz * nz)
            if length <= 0.000001:
                return 0.0, 0.0, 1.0
            return nx / length, ny / length, nz / length

        for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            parts = raw.split()
            if not parts or parts[0].startswith("#"):
                continue
            tag = parts[0]
            if tag == "v" and len(parts) >= 4:
                vertices.append((float(parts[1]), float(parts[2]), float(parts[3])))
            elif tag == "vn" and len(parts) >= 4:
                normals.append((float(parts[1]), float(parts[2]), float(parts[3])))
            elif tag == "usemtl" and len(parts) >= 2:
                material_color = VEHICLE_MATERIAL_COLORS.get(parts[1], DEFAULT_VEHICLE_MATERIAL_COLOR)
            elif tag == "f" and len(parts) >= 4:
                face = [parse_face_token(token) for token in parts[1:]]
                for index in range(1, len(face) - 1):
                    triangle = (face[0], face[index], face[index + 1])
                    points = tuple(vertices[vertex_index] for vertex_index, _ in triangle)
                    fallback_normal = face_normal(points)
                    for vertex_index, normal_index in triangle:
                        vertex = vertices[vertex_index]
                        normal = normals[normal_index] if normal_index is not None else fallback_normal
                        mesh_vertices.extend(vertex)
                        mesh_normals.extend(normal)
                        mesh_colors.extend(material_color)

        vertex_count = len(mesh_vertices) // 3
        if vertex_count < 3 or vertex_count % 3 != 0:
            raise RuntimeError(f"invalid vehicle mesh vertex count: {vertex_count}")

        mesh = rl.Mesh()
        mesh.vertexCount = vertex_count
        mesh.triangleCount = vertex_count // 3
        mesh.vertices = self._alloc_float_array(mesh_vertices)
        mesh.normals = self._alloc_float_array(mesh_normals)
        mesh.colors = self._alloc_uchar_array(mesh_colors)
        return mesh

    def _alloc_float_array(self, values: list[float]):
        data = rl.ffi.cast("float *", rl.mem_alloc(len(values) * rl.ffi.sizeof("float")))
        for index, value in enumerate(values):
            data[index] = value
        return data

    def _alloc_uchar_array(self, values: list[int]):
        data = rl.ffi.cast("unsigned char *", rl.mem_alloc(len(values) * rl.ffi.sizeof("unsigned char")))
        for index, value in enumerate(values):
            data[index] = int(value)
        return data

    def _draw_scene(self, scene: ClusterScene, state: ClusterUiState) -> None:
        camera = rl.Camera3D(
            vec3(scene.camera.position),
            vec3(scene.camera.target),
            rl.Vector3(0.0, 0.0, 1.0),
            scene.camera.fovy_deg,
            rl.CameraProjection.CAMERA_PERSPECTIVE,
        )
        profile_stage = self._profile_start()
        rl.begin_mode_3d(camera)
        self._profile_add("draw_scene.begin_mode_3d", profile_stage)
        rl.rl_push_matrix()
        if abs(scene.scene_shift_x_m) > 0.0001:
            rl.rl_translatef(scene.scene_shift_x_m, 0.0, 0.0)
        try:
            profile_stage = self._profile_start()
            for strip in scene.highlight_lanes:
                self._draw_strip(strip)
            self._profile_add("draw_scene.highlight_lanes", profile_stage)
            profile_stage = self._profile_start()
            for strip in scene.road_edges:
                self._draw_strip(strip)
            self._profile_add("draw_scene.road_edges", profile_stage)
            profile_stage = self._profile_start()
            for strip in scene.lane_markings:
                self._draw_strip(strip)
            self._profile_add("draw_scene.lane_markings", profile_stage)
            profile_stage = self._profile_start()
            for strip in scene.planned_path:
                self._draw_strip(strip)
            self._profile_add("draw_scene.planned_path", profile_stage)
            profile_stage = self._profile_start()
            for point in scene.radar_points:
                self._draw_radar_point(point)
            self._profile_add("draw_scene.radar_points", profile_stage)
            profile_stage = self._profile_start()
            for vehicle in scene.vehicles:
                self._draw_vehicle(vehicle)
            self._profile_add("draw_scene.vehicles", profile_stage)
        finally:
            rl.rl_pop_matrix()
        profile_stage = self._profile_start()
        rl.end_mode_3d()
        self._profile_add("draw_scene.end_mode_3d", profile_stage)
        profile_stage = self._profile_start()
        self._draw_radar_point_labels(
            scene.radar_points,
            camera,
            scene.scene_shift_x_m,
            state.radar_info_mode,
        )
        self._profile_add("draw_scene.radar_labels", profile_stage)
        profile_stage = self._profile_start()
        self._draw_vehicle_badges(
            scene.vehicles,
            camera,
            scene.scene_shift_x_m,
            state.radar_info_mode,
            state.radar_source_color_mode,
        )
        self._profile_add("draw_scene.vehicle_badges", profile_stage)

    def _draw_strip(self, strip: MeshStrip) -> None:
        count = min(len(strip.left), len(strip.right))
        if count < 2:
            return

        color = rl_color(strip.color)
        x_offset_m = strip.x_offset_m

        if hasattr(rl, "draw_triangle_strip_3d"):
            points, point_count = self._triangle_strip_points_for(strip, count)
            point_ptr = rl.ffi.cast("struct Vector3 *", points)
            if x_offset_m != 0.0:
                rl.rl_push_matrix()
                try:
                    rl.rl_translatef(x_offset_m, 0.0, 0.0)
                    rl.draw_triangle_strip_3d(point_ptr, point_count, color)
                finally:
                    rl.rl_pop_matrix()
            else:
                rl.draw_triangle_strip_3d(point_ptr, point_count, color)
            return

        for index in range(count - 1):
            left = strip.left[index]
            right = strip.right[index]
            next_left = strip.left[index + 1]
            next_right = strip.right[index + 1]
            left_near = rl.Vector3(left.x + x_offset_m, left.y, left.z)
            right_near = rl.Vector3(right.x + x_offset_m, right.y, right.z)
            left_far = rl.Vector3(next_left.x + x_offset_m, next_left.y, next_left.z)
            right_far = rl.Vector3(next_right.x + x_offset_m, next_right.y, next_right.z)
            rl.draw_triangle_3d(left_near, right_near, right_far, color)
            rl.draw_triangle_3d(left_near, right_far, left_far, color)

    def _triangle_strip_points_for(self, strip: MeshStrip, count: int):
        key = (id(strip.left), id(strip.right))
        cached = self._triangle_strip_point_cache.get(key)
        if cached is not None:
            left_ref, right_ref, points, point_count = cached
            if left_ref is strip.left and right_ref is strip.right:
                self._triangle_strip_point_cache.move_to_end(key)
                return points, point_count

        point_count = count * 2
        points = rl.ffi.new("struct Vector3[]", point_count)
        for index in range(count):
            left = strip.left[index]
            right = strip.right[index]

            points[index * 2].x = left.x
            points[index * 2].y = left.y
            points[index * 2].z = left.z

            points[index * 2 + 1].x = right.x
            points[index * 2 + 1].y = right.y
            points[index * 2 + 1].z = right.z

        self._triangle_strip_point_cache[key] = (
            strip.left,
            strip.right,
            points,
            point_count,
        )
        while len(self._triangle_strip_point_cache) > TRIANGLE_STRIP_POINT_CACHE_LIMIT:
            self._triangle_strip_point_cache.popitem(last=False)
        return points, point_count

    def _draw_vehicle(self, vehicle: VehicleBox) -> None:
        source_marker = vehicle.source.startswith("modelV2") or vehicle.source in ("radarState", "radarPoint")
        use_model = (
            self._vehicle_model is not None
            and not source_marker
            and (not vehicle.source or vehicle.primary or vehicle.cut_in)
        )
        if use_model:
            self._draw_vehicle_shadow(vehicle)
            self._draw_vehicle_model(vehicle)
            return
        if vehicle.source and (source_marker or (not vehicle.primary and not vehicle.cut_in)):
            self._draw_vehicle_marker(vehicle)
            return
        self._draw_vehicle_box(vehicle)

    def _draw_vehicle_marker(self, vehicle: VehicleBox) -> None:
        alpha = int(80 + 150 * clamp(vehicle.confidence, 0.0, 1.0))
        marker_center = rl.Vector3(vehicle.center.x, vehicle.center.y, vehicle.height_m * 0.32)
        marker_size = rl.Vector3(
            max(0.55, vehicle.width_m * 0.68),
            max(1.05, vehicle.length_m * 0.64),
            max(0.42, vehicle.height_m * 0.45),
        )
        rl.draw_cube_v(marker_center, marker_size, rl_color(vehicle.body_color, alpha))

    def _draw_radar_point(self, point: RadarPointMarker) -> None:
        side_m = max(0.16, point.radius_m * 1.75)
        height_m = max(0.12, point.radius_m * 1.15)
        marker_center = rl.Vector3(point.center.x, point.center.y, point.center.z)
        marker_size = rl.Vector3(side_m, side_m, height_m)
        rl.draw_cube_v(marker_center, marker_size, rl_color(point.color))

    def _draw_radar_point_labels(
        self,
        points: tuple[RadarPointMarker, ...],
        camera,
        scene_shift_x_m: float = 0.0,
        radar_info_mode: int = CLUSTER_RADAR_INFO_ALL_SPEED_DISTANCE,
    ) -> None:
        if not radar_info_shows_radar_points(radar_info_mode):
            return
        theme = self._current_theme()
        profile_enabled = self.profile_enabled
        profile_stage = self._profile_start()
        ordered = sorted(
            points,
            key=lambda point: (point.longitudinal_m, abs(point.lateral_m), point.label),
            reverse=True,
        )
        self._profile_add("draw_scene.radar_labels.sort", profile_stage)

        project_ms = 0.0
        layout_ms = 0.0
        text_ms =