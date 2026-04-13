from pydantic import BaseModel, Field
from typing import List, Optional
from enum import Enum
from decimal import Decimal

class ProjectType(str, Enum):
    TILING = "tiling"
    PAVEMENT = "pavement"
    MASON = "mason"

class MeasurementUnit(str, Enum):
    METERS = "meters"
    FEET = "feet"
    INCHES = "inches"
    CENTIMETERS = "centimeters"

class ProfitType(str, Enum):
    FIXED = "fixed"
    PER_AREA = "per_area"

class RateType(str, Enum):
    DAILY = "daily"
    HOURLY = "hourly"

class RoomBase(BaseModel):
    name: str = "Unnamed Room"
    length: Decimal
    breadth: Decimal
    height: Optional[Decimal] = Decimal(0)

class RoomResponse(RoomBase):
    floor_area: Decimal
    wall_area: Decimal
    total_area: Decimal
    floor_area_with_waste: Decimal
    wall_area_with_waste: Decimal

class WorkerBase(BaseModel):
    role: str
    count: int = 1
    rate: Decimal = Decimal(0)
    rate_type: RateType = RateType.DAILY
    special_equipment_cost_per_day: Decimal = Decimal(0)

class ProjectMaterialBase(BaseModel):
    name: str
    quantity: Decimal = Decimal(0)
    quantity_with_wastage: Decimal = Decimal(0)
    unit: str

class ProjectBase(BaseModel):
    name: str
    project_type: ProjectType = ProjectType.TILING
    measurement_unit: MeasurementUnit = MeasurementUnit.METERS
    profit_type: ProfitType = ProfitType.PER_AREA
    profit_value: Decimal = Decimal(0)
    mortar_thickness: Decimal = Decimal(0)
    wastage_percentage: Optional[Decimal] = None

class ProjectCreate(ProjectBase):
    rooms: List[RoomBase]
    workers: List[WorkerBase]

class ProjectCalculationResults(BaseModel):
    total_floor_area: Decimal
    total_wall_area: Decimal
    total_area: Decimal
    total_area_with_waste: Decimal
    estimated_days: int
    total_labor_cost: Decimal
    material_cost: Decimal
    profit: Decimal
    total_estimate: Decimal
    cost_per_area: Decimal
    materials: List[ProjectMaterialBase]

class ProjectResponse(ProjectBase):
    id: str
    user_id: str
    results: ProjectCalculationResults
    rooms: List[RoomResponse]
    workers: List[WorkerBase]
    status: str = "calculated"
