from datetime import datetime
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw


class ScreenshotService:
    def __init__(self, base_dir: str = "database/screenshots") -> None:
        self.base_path = Path(base_dir)
        self.base_path.mkdir(parents=True, exist_ok=True)

    def _draw_trade_card(self, output_path: Path, title: str, payload: dict[str, Any]) -> str:
        image = Image.new("RGB", (1100, 600), color=(11, 21, 36))
        draw = ImageDraw.Draw(image)

        draw.rectangle([(30, 30), (1070, 570)], outline=(52, 211, 153), width=3)
        draw.text((60, 60), title, fill=(247, 255, 253))
        draw.text((60, 100), datetime.utcnow().isoformat(), fill=(180, 196, 219))

        y = 170
        for key, value in payload.items():
            draw.text((60, y), f"{key}: {value}", fill=(220, 230, 245))
            y += 40

        image.save(output_path)
        return str(output_path)

    def capture_pre_entry(self, trade_ref: str, payload: dict[str, Any]) -> str:
        target = self.base_path / f"{trade_ref}_chart.png"
        return self._draw_trade_card(target, "Pre-Entry Snapshot", payload)

    def capture_post_exit(self, trade_ref: str, payload: dict[str, Any]) -> str:
        target = self.base_path / f"{trade_ref}_result.png"
        return self._draw_trade_card(target, "Post-Exit Snapshot", payload)
