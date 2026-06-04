from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import get_db
from app.models.mcp_server import MCPServer
from app.schemas.mcp_server import MCPServerOut, MCPServerUpdate

router = APIRouter(prefix="/v2/mcp-servers", tags=["mcp"])


@router.get("", response_model=List[MCPServerOut])
async def list_mcp_servers(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(MCPServer).order_by(MCPServer.name))
    return result.scalars().all()


@router.get("/{name}", response_model=MCPServerOut)
async def get_mcp_server(name: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(MCPServer).where(MCPServer.name == name))
    server = result.scalar_one_or_none()
    if not server:
        raise HTTPException(status_code=404, detail="MCP server not found")
    return server


@router.patch("/{name}", response_model=MCPServerOut)
async def update_mcp_server(name: str, body: MCPServerUpdate, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(MCPServer).where(MCPServer.name == name))
    server = result.scalar_one_or_none()
    if not server:
        raise HTTPException(status_code=404, detail="MCP server not found")
    for field, value in body.model_dump(exclude_none=True).items():
        setattr(server, field, value)
    await db.commit()
    await db.refresh(server)
    return server
