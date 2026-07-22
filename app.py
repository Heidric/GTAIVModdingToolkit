import os
import sys
from pathlib import Path

from PySide6.QtWidgets import QApplication, QMainWindow
from qt_material import apply_stylesheet

from build_info import application_title, build_summary
from core.app_logging import (
    configure_application_logging,
    get_application_logger,
    shutdown_application_logging,
)
from ui.main_window import GTAIVEditor
from utils import check_ffmpeg, install_ffmpeg, resource_path


class SplashWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(application_title())
        self.setFixedSize(400, 200)
        self.setStyleSheet("background-color: #121212;")


def run_packaged_smoke_test() -> int:
    required = (
        "assets",
        os.path.join("tools", "ivam.exe"),
        os.path.join("tools", "IVAudioConv.exe"),
    )
    missing = [resource_path(path) for path in required if not os.path.exists(resource_path(path))]
    if missing:
        print("Missing packaged resources:")
        for path in missing:
            print(path)
        return 1

    from PIL import Image  # noqa: F401 - verifies the packaged decoder
    from texfury import Texture  # noqa: F401 - verifies the packaged encoder

    print(build_summary())
    print("Packaged smoke test passed")
    return 0


def main() -> int:
    if "--write-build-info" in sys.argv:
        option_index = sys.argv.index("--write-build-info")
        try:
            output_path = Path(sys.argv[option_index + 1]).expanduser().resolve()
        except IndexError as exc:
            raise SystemExit("--write-build-info requires an output path") from exc
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(build_summary() + "\n", encoding="utf-8")
        return 0

    if "--version" in sys.argv:
        print(build_summary())
        return 0

    if "--smoke-test" in sys.argv:
        return run_packaged_smoke_test()

    log_path = configure_application_logging()
    logger = get_application_logger()
    logger.info("Starting %s", build_summary().replace("\n", " | "))

    try:
        app = QApplication(sys.argv)

        extra = {
            'density_scale': '-2',
            'font_family': 'Montserrat',
            'primaryTextColor': '#FFFFFF',
            'secondaryTextColor': '#B0BEC5',
            'primaryColor': '#000000',
            'secondaryColor': '#424242',
            'accentColor': '#FFC107',
            'backgroundColor': '#121212',
            'windowColor': '#121212',
            'dialogColor': '#212121',
            'borderColor': '#424242',
            'hoverColor': '#FFC107',
            'focusColor': '#FFC107',
            'buttonBackgroundColor': '#FFC107',
            'buttonForegroundColor': '#000000',
            'buttonBorderColor': '#FFC107',
        }

        apply_stylesheet(app, theme='dark_cyan.xml', extra=extra)

        splash = SplashWindow()
        splash.show()

        if not check_ffmpeg():
            logger.warning("FFmpeg was not found through PATH")
            if not install_ffmpeg(splash):
                logger.error("Application startup stopped because FFmpeg is unavailable")
                return 1

        editor = GTAIVEditor()
        editor.show()
        splash.close()
        logger.info("Main window opened; log file: %s", log_path)

        exit_code = app.exec()
        logger.info("Application event loop stopped with exit code %s", exit_code)
        return exit_code
    finally:
        shutdown_application_logging()


if __name__ == "__main__":
    raise SystemExit(main())
