from pydantic import AnyHttpUrl, Field

from .base import PmsBaseModel


class RoomTypeId(PmsBaseModel):
    id: int
    default_code: str = Field("", alias="shortCode")
    icon: AnyHttpUrl | None = None

    @classmethod
    def from_pms_room_type(cls, room_type):
        data = {
            "id": room_type.id,
            "default_code": room_type.default_code,
        }
        if room_type.class_id:
            image_url = cls.url_image_pms_api_rest(
                room_type.env,
                "pms.room.type.class",
                room_type.class_id.id,
                "icon_pms_api_rest",
            )
            if image_url:
                data["icon"] = image_url
        return cls(**data)


class RoomId(PmsBaseModel):
    id: int
    name: str
    roomType: RoomTypeId

    @classmethod
    def from_pms_room(cls, room):
        data = {
            "id": room.id,
            "name": room.name,
        }
        if room.room_type_id:
            data["roomType"] = RoomTypeId.from_pms_room_type(room.room_type_id)
        return cls(**data)
