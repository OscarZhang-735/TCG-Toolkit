"""Select a merchant search adapter for a supported game."""
from .models import Game, SearchSource
from .pagination import PagedSearcher
from .providers.cardmax import CardMaxSEVSearcher, CardMaxWSSearcher
from .providers.hobbystation import HobbyStationLyceeSearcher, HobbyStationWSSearcher, HobbyStationWSRSearcher
from .providers.yuyutei import SEARCHERS


SEARCH_SOURCES = {
    SearchSource.YUYUTEI: SEARCHERS,
    SearchSource.CARDMAX: {Game.SEV: CardMaxSEVSearcher, Game.WS: CardMaxWSSearcher},
    SearchSource.HOBBYSTATION: {searcher.game: searcher for searcher in (
        HobbyStationLyceeSearcher, HobbyStationWSSearcher, HobbyStationWSRSearcher,
    )},
}


def get_searcher_type(source: SearchSource | str, game: Game | str) -> type[PagedSearcher]:
    source, game = SearchSource(source), Game(game)
    searcher_type = SEARCH_SOURCES[source].get(game)
    if searcher_type is None:
        raise ValueError(f"{source} does not support {game}.")
    return searcher_type


def create_searcher(source: SearchSource | str, game: Game | str, *, stock: bool = False) -> PagedSearcher:
    """Create a merchant searcher. stock=True allows extra detail requests for inventory."""
    searcher_type = get_searcher_type(source, game)
    return searcher_type(stock=stock)
