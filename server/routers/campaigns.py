"""Рекламные кампании: обязательство «N показов в день» и контроль план/факт.

Факт считается из play_log (показы роликов рекламодателя в период кампании,
опционально — только на экранах группы). План на дату = target_plays_per_day ×
число прошедших дней кампании. Всё по московскому времени — как расписание
и биллинг (вся сеть в одном часовом поясе, решение от 2026-07-06).
"""
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.orm import Session

from billing.campaign_pricing import (
    billing_mode as parse_billing_mode,
    calculate_campaign_price,
    money,
)
from deps import get_db, get_current_admin, require_write

router = APIRouter()

MSK = timezone(timedelta(hours=3))


def _parse_date(value, field):
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError):
        raise HTTPException(400, f"{field}: ожидается дата в формате YYYY-MM-DD")


def _campaign_facts(db, campaign, today):
    """Показы по дням кампании (МСК-даты) + сводка план/факт на сегодня."""
    # период факта: от начала кампании до min(вчера включительно… точнее —
    # по сегодняшний момент; сегодняшний день учитываем частично как факт,
    # но в план идёт только число ПОЛНЫХ прошедших дней + сегодняшний
    # пропорционально не считаем — упрощение: план на конец сегодняшнего дня).
    d_from = campaign.date_from
    d_to = campaign.date_to
    group_filter = ""
    params = {"aid": campaign.advertiser_id, "df": d_from,
              "dt": d_to + timedelta(days=1)}
    if campaign.group_id is not None:
        group_filter = ("AND pl.screen_id IN "
                        "(SELECT id FROM screens WHERE group_id = :gid)")
        params["gid"] = campaign.group_id

    rows = db.execute(text(f"""
        SELECT (pl.started_at + INTERVAL '3 hours')::date AS on_date,
               COUNT(*) AS plays,
               COALESCE(SUM(EXTRACT(EPOCH FROM (pl.ended_at - pl.started_at))), 0)
                   / 60.0 AS minutes
        FROM play_log pl
        JOIN media m ON m.id = pl.media_id
        WHERE m.advertiser_id = :aid
          AND pl.started_at >= CAST(:df AS date) - INTERVAL '3 hours'
          AND pl.started_at <  CAST(:dt AS date) - INTERVAL '3 hours'
          {group_filter}
        GROUP BY 1 ORDER BY 1
    """), params).fetchall()
    by_day = {
        r.on_date: {
            "plays": int(r.plays or 0),
            "minutes": float(getattr(r, "minutes", 0) or 0),
        }
        for r in rows
    }

    # план/факт на сегодня
    if today < d_from:
        days_due = 0
        status = "scheduled"
    else:
        days_due = (min(today, d_to) - d_from).days + 1
        status = "finished" if today > d_to else "active"
    if not campaign.is_active:
        status = "paused"

    plan_to_date = days_due * campaign.target_plays_per_day
    due_rows = [v for d, v in by_day.items() if d_from <= d <= min(today, d_to)]
    fact_to_date = sum(v["plays"] for v in due_rows)
    minutes_to_date = round(sum(v["minutes"] for v in due_rows), 1)
    pct = round(100.0 * fact_to_date / plan_to_date, 1) if plan_to_date else None

    daily = []
    d = d_from
    while d <= d_to:
        actual = by_day.get(d, {"plays": 0, "minutes": 0.0})
        entry = {"date": d.isoformat(), "plan": campaign.target_plays_per_day,
                 "fact": actual["plays"], "minutes": round(actual["minutes"], 1)}
        entry["future"] = d > today
        daily.append(entry)
        d += timedelta(days=1)

    return {
        "status": status,                       # scheduled | active | finished | paused
        "days_total": (d_to - d_from).days + 1,
        "days_due": days_due,
        "plan_to_date": plan_to_date,
        "fact_to_date": fact_to_date,
        "minutes_to_date": minutes_to_date,
        "fulfillment_pct": pct,
        "shortfall": max(0, plan_to_date - fact_to_date),
        "daily": daily,
    }


@router.post("/campaigns")
def create_campaign(body: dict = Body(...), admin: dict = Depends(require_write),
                    db: Session = Depends(get_db)):
    """
    Тело: {"advertiser_id":1, "name":"Лето-2026", "date_from":"2026-07-01",
           "date_to":"2026-07-31", "target_plays_per_day":500,
           "group_id":null, "note":""}
    """
    adv_id = body.get("advertiser_id")
    name = (body.get("name") or "").strip()
    if not adv_id or not name:
        raise HTTPException(400, "advertiser_id и name обязательны")
    adv = db.execute(text("""
        SELECT id, COALESCE(billing_mode, 'per_minute') AS billing_mode,
               COALESCE(price_per_minute, 0) AS price_per_minute,
               COALESCE(price_per_play, 0) AS price_per_play
        FROM advertisers WHERE id = :id
    """), {"id": adv_id}).fetchone()
    if not adv:
        raise HTTPException(404, "Рекламодатель не найден")
    d_from = _parse_date(body.get("date_from"), "date_from")
    d_to = _parse_date(body.get("date_to"), "date_to")
    if d_to < d_from:
        raise HTTPException(400, "date_to раньше date_from")
    try:
        target = int(body.get("target_plays_per_day"))
    except (TypeError, ValueError):
        raise HTTPException(400, "target_plays_per_day: ожидается целое число > 0")
    if target <= 0:
        raise HTTPException(400, "target_plays_per_day должен быть > 0")
    group_id = body.get("group_id") or None
    if group_id is not None:
        g = db.execute(text("SELECT id FROM sync_groups WHERE id = :id"), {"id": group_id}).fetchone()
        if not g:
            raise HTTPException(404, "Группа не найдена")

    mode = parse_billing_mode(body.get("billing_mode") or adv.billing_mode)
    default_price = adv.price_per_play if mode == "per_play" else adv.price_per_minute
    unit_price = money(
        body.get("unit_price") if body.get("unit_price") not in (None, "") else default_price,
        "unit_price",
    )
    discount = money(body.get("discount_amount") or 0, "discount_amount")
    discount_note = (body.get("discount_note") or "").strip() or None
    if discount and not discount_note:
        raise HTTPException(400, "Для ручной скидки укажите основание")

    row = db.execute(text("""
        INSERT INTO campaigns (advertiser_id, name, date_from, date_to,
                               target_plays_per_day, group_id, note,
                               billing_mode, unit_price, discount_amount,
                               discount_note, pricing_updated_by, pricing_updated_at)
        VALUES (:aid, :n, :df, :dt, :t, :gid, :note,
                :mode, :price, :discount, :discount_note, :actor, NOW())
        RETURNING id
    """), {"aid": adv_id, "n": name, "df": d_from, "dt": d_to, "t": target,
           "gid": group_id, "note": (body.get("note") or "").strip() or None,
           "mode": mode, "price": unit_price, "discount": discount,
           "discount_note": discount_note,
           "actor": admin.get("username", "admin")})
    cid = row.fetchone()[0]
    try:
        db.execute(text("""
            INSERT INTO audit_log (event_type, title, detail, actor)
            VALUES ('campaign', :t, :d, :a)
        """), {"t": f"Создана кампания «{name}»",
               "d": (f"{d_from}—{d_to}, план {target} показов/день; "
                     f"{mode}, {unit_price:.2f} ₽; скидка {discount:.2f} ₽"),
               "a": admin.get("username", "admin")})
    except Exception:
        pass
    db.commit()
    return {"id": cid, "name": name}


@router.get("/campaigns")
def list_campaigns(admin: dict = Depends(get_current_admin), db: Session = Depends(get_db)):
    """Список кампаний со сводкой план/факт (без дневной детализации)."""
    today = datetime.now(MSK).date()
    rows = db.execute(text("""
        SELECT c.*, a.name AS advertiser, g.name AS group_name
        FROM campaigns c
        JOIN advertisers a ON a.id = c.advertiser_id
        LEFT JOIN sync_groups g ON g.id = c.group_id
        ORDER BY c.date_to DESC, c.id DESC
    """)).fetchall()
    out = []
    for c in rows:
        facts = _campaign_facts(db, c, today)
        financial = calculate_campaign_price(
            c.billing_mode, c.unit_price, c.discount_amount,
            facts["fact_to_date"], facts["minutes_to_date"],
        ).as_dict()
        facts.pop("daily")
        out.append({
            "id": c.id, "name": c.name, "advertiser": c.advertiser,
            "advertiser_id": c.advertiser_id,
            "date_from": c.date_from.isoformat(), "date_to": c.date_to.isoformat(),
            "target_plays_per_day": c.target_plays_per_day,
            "group_id": c.group_id, "group_name": c.group_name,
            "is_active": c.is_active, "note": c.note,
            "discount_note": c.discount_note,
            "pricing_updated_by": c.pricing_updated_by,
            "pricing_updated_at": c.pricing_updated_at,
            "financial": financial,
            **facts,
        })
    return out


@router.get("/campaigns/{campaign_id}")
def get_campaign(campaign_id: int, admin: dict = Depends(get_current_admin),
                 db: Session = Depends(get_db)):
    """Кампания с дневной детализацией план/факт."""
    c = db.execute(text("""
        SELECT c.*, a.name AS advertiser, g.name AS group_name
        FROM campaigns c
        JOIN advertisers a ON a.id = c.advertiser_id
        LEFT JOIN sync_groups g ON g.id = c.group_id
        WHERE c.id = :id
    """), {"id": campaign_id}).fetchone()
    if not c:
        raise HTTPException(404, "Кампания не найдена")
    today = datetime.now(MSK).date()
    facts = _campaign_facts(db, c, today)
    financial = calculate_campaign_price(
        c.billing_mode, c.unit_price, c.discount_amount,
        facts["fact_to_date"], facts["minutes_to_date"],
    ).as_dict()
    return {
        "id": c.id, "name": c.name, "advertiser": c.advertiser,
        "date_from": c.date_from.isoformat(), "date_to": c.date_to.isoformat(),
        "target_plays_per_day": c.target_plays_per_day,
        "group_id": c.group_id, "group_name": c.group_name,
        "is_active": c.is_active, "note": c.note,
        "discount_note": c.discount_note,
        "pricing_updated_by": c.pricing_updated_by,
        "pricing_updated_at": c.pricing_updated_at,
        "financial": financial,
        **facts,
    }


@router.patch("/campaigns/{campaign_id}")
def update_campaign(campaign_id: int, body: dict = Body(...),
                    admin: dict = Depends(require_write), db: Session = Depends(get_db)):
    c = db.execute(text("SELECT * FROM campaigns WHERE id = :id"),
                   {"id": campaign_id}).fetchone()
    if not c:
        raise HTTPException(404, "Кампания не найдена")
    sets, params = [], {"id": campaign_id}
    if "name" in body:
        name = (body.get("name") or "").strip()
        if not name:
            raise HTTPException(400, "name не может быть пустым")
        sets.append("name = :n"); params["n"] = name
    if "date_from" in body:
        sets.append("date_from = :df"); params["df"] = _parse_date(body["date_from"], "date_from")
    if "date_to" in body:
        sets.append("date_to = :dt"); params["dt"] = _parse_date(body["date_to"], "date_to")
    if "target_plays_per_day" in body:
        try:
            t = int(body["target_plays_per_day"])
        except (TypeError, ValueError):
            raise HTTPException(400, "target_plays_per_day: целое число > 0")
        if t <= 0:
            raise HTTPException(400, "target_plays_per_day должен быть > 0")
        sets.append("target_plays_per_day = :t"); params["t"] = t
    if "is_active" in body:
        sets.append("is_active = :act"); params["act"] = bool(body["is_active"])
    if "note" in body:
        sets.append("note = :note"); params["note"] = (body.get("note") or "").strip() or None
    pricing_changed = any(k in body for k in (
        "billing_mode", "unit_price", "discount_amount", "discount_note"
    ))
    if "billing_mode" in body:
        sets.append("billing_mode = :mode")
        params["mode"] = parse_billing_mode(body["billing_mode"])
    if "unit_price" in body:
        sets.append("unit_price = :price")
        params["price"] = money(body["unit_price"], "unit_price")
    if "discount_amount" in body:
        sets.append("discount_amount = :discount")
        params["discount"] = money(body["discount_amount"], "discount_amount")
    if "discount_note" in body:
        sets.append("discount_note = :discount_note")
        params["discount_note"] = (body.get("discount_note") or "").strip() or None
    new_discount = params.get("discount", c.discount_amount or 0)
    new_discount_note = params.get("discount_note", c.discount_note)
    if new_discount and not new_discount_note:
        raise HTTPException(400, "Для ручной скидки укажите основание")
    if pricing_changed:
        sets.extend(["pricing_updated_by = :actor", "pricing_updated_at = NOW()"])
        params["actor"] = admin.get("username", "admin")
    if not sets:
        raise HTTPException(400, "Нечего обновлять")
    db.execute(text(f"UPDATE campaigns SET {', '.join(sets)} WHERE id = :id"), params)
    if pricing_changed:
        old_mode = c.billing_mode
        old_price = float(c.unit_price or 0)
        old_discount = float(c.discount_amount or 0)
        new_mode = params.get("mode", old_mode)
        new_price = float(params.get("price", old_price))
        new_discount_value = float(params.get("discount", old_discount))
        db.execute(text("""
            INSERT INTO audit_log (event_type, title, detail, actor)
            VALUES ('campaign', 'Изменены финансовые условия кампании', :detail, :actor)
        """), {
            "detail": (
                f"Кампания #{campaign_id}: {old_mode} {old_price:.2f} ₽, "
                f"скидка {old_discount:.2f} ₽ → {new_mode} {new_price:.2f} ₽, "
                f"скидка {new_discount_value:.2f} ₽"
            ),
            "actor": admin.get("username", "admin"),
        })
    db.commit()
    return {"status": "ok"}


@router.delete("/campaigns/{campaign_id}")
def delete_campaign(campaign_id: int, admin: dict = Depends(require_write),
                    db: Session = Depends(get_db)):
    db.execute(text("DELETE FROM campaigns WHERE id = :id"), {"id": campaign_id})
    db.commit()
    return {"status": "deleted"}
