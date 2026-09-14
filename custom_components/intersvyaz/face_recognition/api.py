import aiohttp
from homeassistant.exceptions import HomeAssistantError

class ApiFaceRecognitionBackend(FaceRecognitionBackend):
    def __init__(
        self,
        url: str,
        token: str | None,
        confidence_threshold: float,
    ):
        self._url = url
        self._token = token
        self._confidence_threshold = confidence_threshold

    async def recognize(self, image: bytes) -> Optional[str]:
        headers = {}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"

        form = aiohttp.FormData()
        form.add_field(
            "image",
            image,
            filename="snapshot.jpg",
            content_type="image/jpeg",
        )

        async with aiohttp.ClientSession() as session:
            async with session.post(
                self._url,
                data=form,
                headers=headers,
            ) as resp:
                if resp.status != 200:
                    raise HomeAssistantError(
                        f"Face API error HTTP {resp.status}"
                    )

                data = await resp.json()

        if not data.get("matched"):
            return None

        if data.get("confidence", 1.0) < self._confidence_threshold:
            return None

        return data.get("name")
