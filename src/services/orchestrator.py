# Custom modules — V1 (FastF1)
from src.services.analysis.v1.top_speed import TopSpeedPlot, TopSpeedData
from src.services.analysis.v1.throttle_comparison import ThrottleComp, ThrottleCompData
from src.services.analysis.v1.qualifying_results import QualiResults, QualiResultsData
from src.services.analysis.v1.track_comparison import TrackComparisonPlot, TrackComparisonData
from src.services.analysis.v1.throttle_brake_comparison import throttle_graph, throttle_graph_data
from src.services.analysis.v1.laptimes_distribution import LatimesDistribution

# Custom modules — V2 (F1StaticClient, no FastF1 dependency)
from src.services.analysis.v2.top_speed import TopSpeedData_Telemetry as V2_TopSpeedData
from src.services.analysis.v2.throttle_comparison import ThrottleCompData as V2_ThrottleCompData
from src.services.analysis.v2.qualifying_results import QualiResultsData as V2_QualiResultsData

from src.services.analysis.base import with_fallback


def _top_speed_with_fallback(year, round_num, session_name):
    return with_fallback(
        primary=lambda: TopSpeedData(year, round_num, session_name),
        secondary=lambda: V2_TopSpeedData(year, round_num, session_name),
        primary_source="fastf1", secondary_source="livetiming",
        year=year, gp=round_num, session=session_name, data_type="top_speed",
    )


def _throttle_with_fallback(year, round_num, session_name):
    return with_fallback(
        primary=lambda: ThrottleCompData(year, round_num, session_name),
        secondary=lambda: V2_ThrottleCompData(year, round_num, session_name),
        primary_source="fastf1", secondary_source="livetiming",
        year=year, gp=round_num, session=session_name, data_type="throttle_comparison",
    )


def get_session_category(session_name):
    """Determine the category of session."""
    session_categories = {
        'FP1': 'practice',
        'FP2': 'practice',
        'FP3': 'practice',
        'Q': 'qualifying',
        'SQ': 'sprint_qualifying',
        'Sprint': 'sprint',
        'R': 'race'
    }
    return session_categories.get(session_name, 'practice')


def latest_session_analised(latest_session):
    """
    Analyze the latest session and return concatenated data based on session type.

    Args:
        latest_session: Dict containing 'year', 'round', and 'session_name'

    Returns:
        Dict with all analyzed data for the session type
    """
    year = latest_session['year']
    round_num = latest_session['round']
    session_name = latest_session['session_name']

    # Determine session category
    category = get_session_category(session_name)

    # Route to appropriate analysis function
    if category == 'practice':
        return analise_practice(year, round_num, session_name)
    elif category in ['qualifying', 'sprint_qualifying']:
        return analise_qualifying(year, round_num, session_name)
    elif category == 'sprint':
        return analise_sprint(year, round_num, session_name)
    elif category == 'race':
        return analise_race(year, round_num, session_name)
    else:
        return analise_practice(year, round_num, session_name)


def analise_practice(year, round_num, session_name):
    """
    Analyze practice sessions (FP1, FP2, FP3).
    Returns: top speed and throttle comparison data.
    """
    data = {
        'session_type': 'practice',
        'year': year,
        'round': round_num,
        'session_name': session_name
    }

    try:
        data['top_speed'] = _top_speed_with_fallback(year, round_num, session_name)
    except Exception as e:
        data['top_speed'] = {'error': str(e)}

    try:
        data['throttle_comparison'] = _throttle_with_fallback(year, round_num, session_name)
    except Exception as e:
        data['throttle_comparison'] = {'error': str(e)}

    return data


def analise_qualifying(year, round_num, session_name):
    """
    Analyze qualifying sessions (Q, SQ).
    Returns: qualifying results, top speed, and throttle comparison data.
    """
    data = {
        'session_type': 'qualifying',
        'year': year,
        'round': round_num,
        'session_name': session_name
    }

    # Qualifying results — V1 only (no V2 equivalent yet)
    try:
        data['qualifying_results'] = QualiResultsData(year, round_num, session_name)
    except Exception as e:
        data['qualifying_results'] = {'error': str(e), 'note': 'V2 fallback unavailable for qualifying results'}

    try:
        data['top_speed'] = _top_speed_with_fallback(year, round_num, session_name)
    except Exception as e:
        data['top_speed'] = {'error': str(e)}

    try:
        data['throttle_comparison'] = _throttle_with_fallback(year, round_num, session_name)
    except Exception as e:
        data['throttle_comparison'] = {'error': str(e)}

    return data


def analise_sprint(year, round_num, session_name):
    """
    Analyze sprint sessions.
    Returns: top speed and throttle comparison data.
    """
    data = {
        'session_type': 'sprint',
        'year': year,
        'round': round_num,
        'session_name': session_name
    }

    try:
        data['top_speed'] = _top_speed_with_fallback(year, round_num, session_name)
    except Exception as e:
        data['top_speed'] = {'error': str(e)}

    try:
        data['throttle_comparison'] = _throttle_with_fallback(year, round_num, session_name)
    except Exception as e:
        data['throttle_comparison'] = {'error': str(e)}

    # TODO: Add sprint-specific analyses:
    # - Lap-by-lap positions
    # - Race pace analysis
    # - Overtakes analysis

    return data


def analise_race(year, round_num, session_name):
    """
    Analyze race sessions.
    Returns: top speed, throttle comparison, and race-specific data.
    """
    data = {
        'session_type': 'race',
        'year': year,
        'round': round_num,
        'session_name': session_name
    }

    try:
        data['top_speed'] = _top_speed_with_fallback(year, round_num, session_name)
    except Exception as e:
        data['top_speed'] = {'error': str(e)}

    try:
        data['throttle_comparison'] = _throttle_with_fallback(year, round_num, session_name)
    except Exception as e:
        data['throttle_comparison'] = {'error': str(e)}

    # TODO: Add race-specific analyses:
    # - Race results/classification
    # - Lap-by-lap positions
    # - Pit stop strategies
    # - Tire strategies
    # - Race pace comparison
    # - Overtakes analysis

    return data


# ============================================================================
# V2 dashboard dispatch — livetiming-only, no FastF1 fallback.
# ============================================================================

def latest_session_analised_v2(latest_session):
    """V2 sibling of :func:`latest_session_analised`. Same response contract,
    but every data block is produced via the livetiming-only V2 path."""
    year = latest_session['year']
    round_num = latest_session['round']
    session_name = latest_session['session_name']

    category = get_session_category(session_name)

    if category == 'practice':
        return _analise_practice_v2(year, round_num, session_name)
    elif category in ['qualifying', 'sprint_qualifying']:
        return _analise_qualifying_v2(year, round_num, session_name)
    elif category == 'sprint':
        return _analise_sprint_v2(year, round_num, session_name)
    elif category == 'race':
        return _analise_race_v2(year, round_num, session_name)
    else:
        return _analise_practice_v2(year, round_num, session_name)


def _analise_practice_v2(year, round_num, session_name):
    data = {'session_type': 'practice', 'year': year, 'round': round_num, 'session_name': session_name}
    try:
        data['top_speed'] = V2_TopSpeedData(year, round_num, session_name)
    except Exception as e:
        data['top_speed'] = {'error': str(e)}
    try:
        data['throttle_comparison'] = V2_ThrottleCompData(year, round_num, session_name)
    except Exception as e:
        data['throttle_comparison'] = {'error': str(e)}
    return data


def _analise_qualifying_v2(year, round_num, session_name):
    data = {'session_type': 'qualifying', 'year': year, 'round': round_num, 'session_name': session_name}
    try:
        data['qualifying_results'] = V2_QualiResultsData(year, round_num, session_name)
    except Exception as e:
        data['qualifying_results'] = {'error': str(e)}
    try:
        data['top_speed'] = V2_TopSpeedData(year, round_num, session_name)
    except Exception as e:
        data['top_speed'] = {'error': str(e)}
    try:
        data['throttle_comparison'] = V2_ThrottleCompData(year, round_num, session_name)
    except Exception as e:
        data['throttle_comparison'] = {'error': str(e)}
    return data


def _analise_sprint_v2(year, round_num, session_name):
    data = {'session_type': 'sprint', 'year': year, 'round': round_num, 'session_name': session_name}
    try:
        data['top_speed'] = V2_TopSpeedData(year, round_num, session_name)
    except Exception as e:
        data['top_speed'] = {'error': str(e)}
    try:
        data['throttle_comparison'] = V2_ThrottleCompData(year, round_num, session_name)
    except Exception as e:
        data['throttle_comparison'] = {'error': str(e)}
    return data


def _analise_race_v2(year, round_num, session_name):
    data = {'session_type': 'race', 'year': year, 'round': round_num, 'session_name': session_name}
    try:
        data['top_speed'] = V2_TopSpeedData(year, round_num, session_name)
    except Exception as e:
        data['top_speed'] = {'error': str(e)}
    try:
        data['throttle_comparison'] = V2_ThrottleCompData(year, round_num, session_name)
    except Exception as e:
        data['throttle_comparison'] = {'error': str(e)}
    return data
