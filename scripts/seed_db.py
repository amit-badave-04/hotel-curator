"""Seed the database from NB04 parquet outputs."""

import pandas as pd
from sqlmodel import Session, SQLModel, create_engine

from hotel_curator.db.models import HotelRecord
from hotel_curator.settings import settings


def seed() -> None:
    """Load hotel_signals.parquet into the database."""
    engine = create_engine(
        settings.database_url.replace("+aiosqlite", ""),
    )
    SQLModel.metadata.create_all(engine)
    df = pd.read_parquet("data/processed/hotel_signals.parquet")
    with Session(engine) as session:
        for _, row in df.iterrows():
            record = HotelRecord(
                hotel_id=int(row["hotel_id"]),
                tier=row.get("tier", "UNKNOWN"),
                n_reviews=int(row["n_reviews"]),
                avg_score=float(row["avg_score"]),
                weighted_avg_score=float(
                    row.get("weighted_avg_score", 0),
                ),
                min_critical_score=float(
                    row["min_critical_score"],
                ),
            )
            session.merge(record)
        session.commit()
    print(f"Seeded {len(df)} hotels.")


if __name__ == "__main__":
    seed()
