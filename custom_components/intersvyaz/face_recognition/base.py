from abc import ABC, abstractmethod
from typing import Optional

class FaceRecognitionBackend(ABC):
    @abstractmethod
    async def recognize(self, image: bytes) -> Optional[str]:
        """Вернуть имя лица или None"""
