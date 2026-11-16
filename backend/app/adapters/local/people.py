"""People directory backed by the standalone ``app_user`` table."""

import uuid

from sqlalchemy import or_, select
from sqlalchemy.dialects.postgresql import array
from sqlalchemy.orm import Session

from app.db.models import AppUser
from app.ports.people import Person


def _person(u: AppUser) -> Person:
    return Person(user_id=str(u.id), name=u.name, email=u.email, roles=tuple(u.roles))


class DbPeopleDirectory:
    def search(self, tenant_id: str, *, roles: tuple[str, ...] = (), query: str = "",
               limit: int = 50, session: Session | None = None) -> list[Person]:
        if session is None:
            raise ValueError("DbPeopleDirectory needs a DB session")
        stmt = select(AppUser).where(AppUser.tenant_id == tenant_id, AppUser.is_active.is_(True))
        if roles:
            stmt = stmt.where(AppUser.roles.op("?|")(array(list(roles))))
        q = query.strip()
        if q:
            like = f"%{q}%"
            stmt = stmt.where(or_(AppUser.name.ilike(like), AppUser.email.ilike(like)))
        users = session.scalars(stmt.order_by(AppUser.name).limit(limit)).all()
        return [_person(u) for u in users]

    def get(self, tenant_id: str, user_id: str, *, session: Session | None = None) -> Person | None:
        if session is None:
            raise ValueError("DbPeopleDirectory needs a DB session")
        try:
            uid = uuid.UUID(user_id)
        except ValueError:
            return None
        user = session.scalar(select(AppUser).where(AppUser.id == uid, AppUser.tenant_id == tenant_id,
                                                    AppUser.is_active.is_(True)))
        return _person(user) if user else None
