from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Device, MaintenanceContract
from app.schemas import (
    MaintenanceContractCreate,
    MaintenanceContractRead,
    MaintenanceContractUpdate,
)

router = APIRouter(tags=["maintenance"])

DbSession = Annotated[Session, Depends(get_db)]


@router.get("/devices/{device_id}/contracts", response_model=list[MaintenanceContractRead])
def list_contracts(device_id: int, db: DbSession) -> list[MaintenanceContract]:
    if db.get(Device, device_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"device {device_id} が見つかりません")
    stmt = (
        select(MaintenanceContract)
        .where(MaintenanceContract.device_id == device_id)
        .order_by(MaintenanceContract.end_date)
    )
    return list(db.scalars(stmt))


@router.post(
    "/devices/{device_id}/contracts",
    response_model=MaintenanceContractRead,
    status_code=status.HTTP_201_CREATED,
)
def create_contract(
    device_id: int, payload: MaintenanceContractCreate, db: DbSession
) -> MaintenanceContract:
    if db.get(Device, device_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"device {device_id} が見つかりません")
    contract = MaintenanceContract(device_id=device_id, **payload.model_dump())
    db.add(contract)
    db.commit()
    db.refresh(contract)
    return contract


def _load_contract(db: Session, contract_id: int) -> MaintenanceContract:
    contract = db.get(MaintenanceContract, contract_id)
    if contract is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"contract {contract_id} が見つかりません")
    return contract


@router.patch("/contracts/{contract_id}", response_model=MaintenanceContractRead)
def update_contract(
    contract_id: int, payload: MaintenanceContractUpdate, db: DbSession
) -> MaintenanceContract:
    contract = _load_contract(db, contract_id)
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(contract, key, value)

    if contract.end_date < contract.start_date:
        db.rollback()
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, "end_date は start_date 以降である必要があります"
        )

    db.commit()
    db.refresh(contract)
    return contract


@router.delete("/contracts/{contract_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_contract(contract_id: int, db: DbSession) -> None:
    db.delete(_load_contract(db, contract_id))
    db.commit()
