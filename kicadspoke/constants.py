# kicadspoke/constants.py

# --- Поля и роли ---
ROLE_FIELD_NAME = "Role"
# Второе кастомное поле схемы, рядом с Role — физический экземпляр/кластер
# (не нативный KiCad Group — сознательно, см. обсуждение в чате: не хотим
# делить пространство данных с чужой, ещё дорабатываемой multichannel-
# механикой KiCad). Иерархия ("Channel_1/1V2_PLL_PI_FILTER") поддерживается
# бесплатно через сравнение по префиксу сегментов — плоское имя без "/"
# просто вырождается в частный случай точного совпадения.
CLUSTER_FIELD_NAME = "Cluster"

# --- Допуски ---
POSITION_TOLERANCE_NM = 10_000       # 0.01 мм
ANGLE_TOLERANCE_DEG = 0.1
POSITION_TOLERANCE_MM = 0.01

# --- Параметры по умолчанию ---
DEFAULT_BATCH_SIZE = 10
DEFAULT_TIMEOUT_MS = 20000
DEFAULT_LOG_DIR = "logs"

# --- Реестр ---
SPOKE_LEVEL_ROLE_PLACEHOLDER = "__spoke__"