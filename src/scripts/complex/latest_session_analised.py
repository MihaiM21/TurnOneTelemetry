# Custom modules
from src.scripts.simple.top_speed import TopSpeedPlot, TopSpeedData
from src.scripts.simple.throttle_comparison import ThrottleComp, ThrottleCompData
from src.scripts.quali_practice.qulifying_results import QualiResults, QualiResultsData
from src.scripts.quali_practice.track_comparison_2drivers import TrackComparisonPlot, TrackComparisonData
from src.scripts.quali_practice.throttleBrake_comparison_2drivers import throttle_graph, throttle_graph_data
from src.scripts.simple.laptimes_distribution import LatimesDistribution


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

    # Top Speed Analysis
    try:
        data['top_speed'] = TopSpeedData(year, round_num, session_name)
    except Exception as e:
        data['top_speed'] = {'error': str(e)}

    # Throttle Comparison Analysis
    try:
        data['throttle_comparison'] = ThrottleCompData(year, round_num, session_name)
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

    # Qualifying Results
    try:
        data['qualifying_results'] = QualiResultsData(year, round_num, session_name)
    except Exception as e:
        data['qualifying_results'] = {'error': str(e)}

    # Top Speed Analysis
    try:
        data['top_speed'] = TopSpeedData(year, round_num, session_name)
    except Exception as e:
        data['top_speed'] = {'error': str(e)}

    # Throttle Comparison Analysis
    try:
        data['throttle_comparison'] = ThrottleCompData(year, round_num, session_name)
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

    # Top Speed Analysis
    try:
        data['top_speed'] = TopSpeedData(year, round_num, session_name)
    except Exception as e:
        data['top_speed'] = {'error': str(e)}

    # Throttle Comparison Analysis
    try:
        data['throttle_comparison'] = ThrottleCompData(year, round_num, session_name)
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

    # Top Speed Analysis
    try:
        data['top_speed'] = TopSpeedData(year, round_num, session_name)
    except Exception as e:
        data['top_speed'] = {'error': str(e)}

    # Throttle Comparison Analysis
    try:
        data['throttle_comparison'] = ThrottleCompData(year, round_num, session_name)
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
