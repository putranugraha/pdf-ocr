import cv2
import numpy as np
from fastapi.testclient import TestClient

from app.main import app


def test_pdf_job_preserves_filename_and_is_one_time():
    image = np.full((300, 200, 3), 245, dtype=np.uint8)
    cv2.putText(image, "697", (35, 160), cv2.FONT_HERSHEY_SIMPLEX, 1.8, (0, 0, 0), 4)
    ok, encoded = cv2.imencode(".jpg", image)
    assert ok

    client = TestClient(app)
    filename = "697 220208010926Y003452.pdf"
    job = client.post(
        "/api/pdf-job",
        files=[("pages", ("page-1.jpg", encoded.tobytes(), "image/jpeg"))],
        data={"filename": filename},
    )
    assert job.status_code == 200
    payload = job.json()
    assert payload["filename"] == filename
    assert payload["download_url"].endswith("/" + filename.replace(" ", "%20"))

    download = client.get(payload["download_url"])
    assert download.status_code == 200
    assert download.content.startswith(b"%PDF")
    assert f'filename="{filename}"' in download.headers["content-disposition"]
    assert client.get(payload["download_url"]).status_code == 404
