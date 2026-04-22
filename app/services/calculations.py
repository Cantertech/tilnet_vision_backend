import math
from decimal import Decimal
from typing import List, Tuple, Dict
from app.schemas.project import ProjectCreate, ProjectCalculationResults, RoomResponse, ProjectMaterialBase, ProjectType, ProfitType

# --- LEGACY CONSTANTS ---
CONVERSION_FACTORS = {
    'meters': Decimal('1'),
    'feet': Decimal('0.3048'),
    'inches': Decimal('0.0254'),
    'centimeters': Decimal('0.01'),
    'millimeters': Decimal('0.001')
}

COVERAGE_RATES = {
    'tiling': {
        'cement': Decimal('1') / Decimal('6'),    
        'sand': Decimal('1.4') / Decimal('6'),        
        'chemical': Decimal('1') / Decimal('6.72'),      
        'tile cement': Decimal('1') / Decimal('4'),    
        'grout': Decimal('1') / Decimal('4.6666'), 
    },
    'pavement': {
        'cement': Decimal('1') / Decimal('5'),
        'rough sand': Decimal('1') / Decimal('1.5'),
        'grouting cement': Decimal('1') / Decimal('450'),
    }
}

MATERIAL_PRICES = {
    'cement': Decimal('95'),
    'sand': Decimal('350'),
    'tile cement': Decimal('65'),
    'chemical': Decimal('55'),
    'grout': Decimal('15'),
    'rough sand': Decimal('280'),
    'grouting cement': Decimal('12')
}

ROLE_SPEEDS = {
    'master': {'floor': Decimal('30'), 'wall': Decimal('20')},
    'labourer': {'floor': Decimal('0'), 'wall': Decimal('0')},
    'painter': {'floor': Decimal('0'), 'wall': Decimal('120')},
    'default': {'floor': Decimal('10'), 'wall': Decimal('10')},
}

class CalculationService:
    def get_wastage_multiplier(self, area: Decimal) -> Decimal:
        if area <= 55: percent = 15
        elif area <= 200: percent = 12
        else: percent = 10
        return Decimal('1.00') + (Decimal(str(percent)) / Decimal('100'))

    def convert_to_meters(self, val: Decimal, unit: str) -> Decimal:
        return val * CONVERSION_FACTORS.get(unit.lower(), Decimal('1'))

    def convert_sand_unit(self, qty: Decimal) -> Tuple[Decimal, str]:
        if qty >= 300: return (qty / Decimal('300')).quantize(Decimal('1.01')), "large tipper"
        if qty >= 175: return (qty / Decimal('175')).quantize(Decimal('1.01')), "small tipper"
        if qty >= 1: return qty.quantize(Decimal('1.01')), "wheelbarrow"
        return (qty * Decimal('8')).quantize(Decimal('1.01')), "headpan"

    def calculate_project(self, data: ProjectCreate, user_materials: List[dict] = None) -> Tuple[List[RoomResponse], ProjectCalculationResults]:
        processed_rooms = []
        total_f_w, total_w_w = Decimal('0'), Decimal('0')
        total_f, total_w = Decimal('0'), Decimal('0')

        # 1. Area Calculation
        for room in data.rooms:
            unit_str = data.measurement_unit.value if hasattr(data.measurement_unit, 'value') else str(data.measurement_unit)
            l_m = self.convert_to_meters(room.length, unit_str)
            b_m = self.convert_to_meters(room.breadth, unit_str)
            h_m = self.convert_to_meters(room.height or Decimal('0'), unit_str)

            f_area = l_m * b_m
            w_area = (Decimal('2') * l_m + Decimal('2') * b_m) * h_m
            
            f_mult = self.get_wastage_multiplier(f_area)
            w_mult = self.get_wastage_multiplier(w_area)

            processed_rooms.append(RoomResponse(
                **room.model_dump(),
                floor_area=f_area,
                wall_area=w_area,
                total_area=f_area + w_area,
                floor_area_with_waste=(f_area * f_mult).quantize(Decimal('1.01')),
                wall_area_with_waste=(w_area * w_mult).quantize(Decimal('1.01'))
            ))
            total_f += f_area
            total_w += w_area
            total_f_w += (f_area * f_mult)
            total_w_w += (w_area * w_mult)

        # 2. Materials Core Calculation
        materials_dict: Dict[str, ProjectMaterialBase] = {}
        proj_type_val = data.project_type.value if hasattr(data.project_type, 'value') else str(data.project_type)
        project_rates = COVERAGE_RATES.get(proj_type_val, COVERAGE_RATES['tiling'])
        total_area_w = total_f_w + total_w_w
        
        # Determine Adhesive choice from user notes
        use_tile_cement = False
        user_mat_list = user_materials or []
        for um in user_mat_list:
            if "tile cement" in (um.get("name") or "").lower():
                use_tile_cement = True

        # Define Mandatory Set
        items_to_calc = ['cement', 'sand', 'grout']
        if proj_type_val == 'tiling':
            items_to_calc.append('tile cement' if use_tile_cement else 'chemical')
        elif proj_type_val == 'pavement':
            items_to_calc = ['cement', 'rough sand', 'grouting cement']

        # A. Automated Estimation
        for item_name in items_to_calc:
            rate = project_rates.get(item_name, Decimal('0'))
            raw_qty = total_area_w * rate
            if data.mortar_thickness >= Decimal('9.88'):
                raw_qty *= Decimal('1.07')

            final_qty, unit = raw_qty, "bags"
            if item_name in ['sand', 'rough sand']:
                final_qty, unit = self.convert_sand_unit(raw_qty)
            elif item_name == 'grout':
                final_qty, unit = (raw_qty / Decimal('3')).quantize(Decimal('1')), "bags"
            
            materials_dict[item_name] = ProjectMaterialBase(
                name=item_name.title(),
                quantity=raw_qty.quantize(Decimal('1.01')),
                quantity_with_wastage=final_qty.quantize(Decimal('1.01')),
                unit=unit
            )

        # B. User Material Integration (Merging)
        final_materials = []
        for um in user_mat_list:
            name = (um.get("name") or "Item").title()
            name_lower = name.lower()
            
            # If tiler provided quantity/price, override or add
            if name_lower in materials_dict:
                # Merge: Prioritize User defined values if they provided them
                target = materials_dict[name_lower]
                if um.get("qty"): target.quantity_with_wastage = Decimal(str(um.get("qty")))
                # We'll calculate price total later
                final_materials.append({**target.model_dump(), "price": Decimal(str(um.get("price") or 0))})
                del materials_dict[name_lower]
            else:
                # New individual item
                final_materials.append({
                    "name": name,
                    "quantity": Decimal(str(um.get("qty") or 0)),
                    "quantity_with_wastage": Decimal(str(um.get("qty") or 0)),
                    "unit": um.get("unit", "pcs"),
                    "price": Decimal(str(um.get("price") or 0))
                })

        # Add remaining calculated materials that weren't in user notes
        for m in materials_dict.values():
            name_lower = m.name.lower()
            default_price = MATERIAL_PRICES.get(name_lower, Decimal('0'))
            final_materials.append({**m.model_dump(), "price": default_price})

        # 3. Estimated Days & Labor
        total_f_speed, total_w_speed = Decimal('0'), Decimal('0')
        for worker in data.workers:
            speeds = ROLE_SPEEDS.get(worker.role.lower(), ROLE_SPEEDS['default'])
            total_f_speed += speeds['floor'] * worker.count
            total_w_speed += speeds['wall'] * worker.count

        f_days = (total_f / total_f_speed) if total_f_speed > 0 else Decimal('0')
        w_days = (total_w / total_w_speed) if total_w_speed > 0 else Decimal('0')
        est_days = math.ceil(f_days + w_days)

        labor_cost = Decimal('0')
        for worker in data.workers:
            mult = Decimal('1') if worker.rate_type == 'daily' else Decimal('8')
            labor_cost += (worker.rate * worker.count * Decimal(str(est_days)) * mult)
            labor_cost += (worker.special_equipment_cost_per_day * Decimal(str(est_days)))

        # 4. Financials
        total_price = data.profit_value if data.profit_type == ProfitType.FIXED else (data.profit_value * total_area_w)
        
        # Calculate Material Sum
        mat_total = sum([m['price'] * m['quantity_with_wastage'] for m in final_materials])

        return processed_rooms, ProjectCalculationResults(
            total_floor_area=total_f,
            total_wall_area=total_w,
            total_area=total_f + total_w,
            total_area_with_waste=total_area_w,
            estimated_days=int(est_days),
            total_labor_cost=labor_cost,
            material_cost=mat_total,
            profit=total_price,
            total_estimate=labor_cost + mat_total + total_price,
            cost_per_area=((labor_cost + mat_total + total_price) / total_area_w) if total_area_w > 0 else Decimal('0'),
            materials=final_materials # Now includes prices and merged items
        )
