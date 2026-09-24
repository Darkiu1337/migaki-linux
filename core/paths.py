import os

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
SCRIPTS_DIR = os.path.join(REPO_ROOT, "scripts")
SHADERS_DIR = os.path.join(REPO_ROOT, "shaders")
PRESETS_JSON = os.path.join(SHADERS_DIR, "presets.json")
TRANSLATE_DIR = os.path.join(REPO_ROOT, "translate")

CONFIG_DIR = os.path.expanduser("~/.config/migaki")
GAMES_JSON = os.path.join(CONFIG_DIR, "games.json")
CONFIG_JSON = os.path.join(CONFIG_DIR, "config.json")
ICON_CACHE = os.path.expanduser("~/.cache/migaki/icons")
GPU_CACHE = os.path.expanduser("~/.cache/migaki/gpus.json")

DEFAULT_PREFIX = os.path.join(os.path.expanduser("~"), ".local/share/migaki/prefixes/default")

RUNNERS = {
    "proton": "Windows games (D3D9-12/Vulkan filtered; OpenGL runs unfiltered)",
    "rpgmaker": "RPGMaker dirs (MV/MZ filtered; other engines redirect)",
    "native": "Linux executables (Vulkan direct; OpenGL via Zink; 64-bit only)",
}

VARIANT_NOTES = {
    "L": "strongest, highest GPU cost",
    "M": "balanced",
    "S": "lightest, cheapest",
    "Soft_S": "for aliased art, light",
    "Soft_M": "for aliased art, balanced",
    "Soft_L": "for aliased art, strong",
    "VL": "very large network, heavy",
    "UL": "ultra large network, heaviest",
    "Soft_VL": "soft-tuned very large, heavy",
    "Soft_UL": "soft-tuned ultra large, heaviest",
    # Clear presets (3D clarity); the manifest's own `note` is authoritative.
    "Clear": "3D: sharpen soft/TAA image (neutral color)",
    "Clear_Vivid": "3D: sharpness + color pop (washed-out art)",
    "Clear_AA": "3D: anti-aliasing + sharpness",
}

LOCALES = (
    "ja_JP.UTF-8", "zh_CN.UTF-8", "zh_TW.UTF-8", "ko_KR.UTF-8",
    "fr_FR.UTF-8", "de_DE.UTF-8", "es_ES.UTF-8", "pt_BR.UTF-8",
    "ru_RU.UTF-8",
)

VKBASALT_ENV_VARS = ("VK_ADD_LAYER_PATH", "VK_INSTANCE_LAYERS",
                     "ENABLE_VKBASALT", "VKBASALT_CONFIG_FILE")

TEXTBOX_PROG = "textbox.py"
