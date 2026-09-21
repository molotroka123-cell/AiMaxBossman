"""Read-only catalogue; effects use canonical VideoService commands and render."""
from fastapi import APIRouter
from . import Feature
from ..video_studio.viral_vfx import catalog

router = APIRouter()

@router.get('/video-studio/vfx-catalog')
async def vfx_catalog():
    return catalog()

FEATURE = Feature(name='viral_vfx', router=router)
