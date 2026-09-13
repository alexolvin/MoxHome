# Заглушка langdetect 1.0.9 (нет py.typed): только реально используемый API (L0.4).
# Явный re-export (`as`) — mypy --strict включает --no-implicit-reexport.
from langdetect.detector_factory import (
    PROFILES_DIRECTORY as PROFILES_DIRECTORY,
    Detector as Detector,
    DetectorFactory as DetectorFactory,
    Language as Language,
    detect as detect,
    detect_langs as detect_langs,
)
from langdetect.lang_detect_exception import LangDetectException as LangDetectException
