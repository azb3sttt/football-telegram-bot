from dataclasses import dataclass

from app.config import Settings
from app.db.repository import Repository
from app.services.analysis import MatchAnalyzer
from app.services.football_api import FootballApiClient
from app.services.league_catalog import LeagueCatalog
from app.services.reports import ReportService


@dataclass(slots=True)
class Services:
    settings: Settings
    repo: Repository
    api: FootballApiClient
    analyzer: MatchAnalyzer
    reports: ReportService
    leagues: LeagueCatalog
