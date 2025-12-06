from datetime import datetime

# Define the data with timezone-aware datetimes
# Note: All times are maintained in UTC (Universal Time Coordinated)
f1_2025_races_data = [
  # Australia
  {
    "grandPrix": "Australian Grand Prix",
    "circuit": "Melbourne Grand Prix Circuit",
    "country": "Australia",
    "hasSprint": False,
    "sessions": [
      {
        "name": "Free Practice 1",
        "startTime": datetime.fromisoformat("2025-03-13T17:30:00+00:00"),
        "endTime": datetime.fromisoformat("2025-03-13T18:30:00+00:00"),
      },
      {
        "name": "Free Practice 2",
        "startTime": datetime.fromisoformat("2025-03-13T21:00:00+00:00"),
        "endTime": datetime.fromisoformat("2025-03-13T22:00:00+00:00"),
      },
      {
        "name": "Free Practice 3",
        "startTime": datetime.fromisoformat("2025-03-14T17:30:00+00:00"),
        "endTime": datetime.fromisoformat("2025-03-14T18:30:00+00:00"),
      },
      {
        "name": "Qualifying",
        "startTime": datetime.fromisoformat("2025-03-14T21:00:00+00:00"),
        "endTime": datetime.fromisoformat("2025-03-14T22:00:00+00:00"),
      },
      {
        "name": "Race",
        "startTime": datetime.fromisoformat("2025-03-15T20:00:00+00:00"),
        "endTime": datetime.fromisoformat("2025-03-15T22:00:00+00:00"),
      },
    ],
  },
  # China
  {
    "grandPrix": "Chinese Grand Prix",
    "circuit": "Shanghai International Circuit",
    "country": "China",
    "hasSprint": True,
    "sessions": [
      {
        "name": "Free Practice 1",
        "startTime": datetime.fromisoformat("2025-03-20T19:30:00+00:00"),
        "endTime": datetime.fromisoformat("2025-03-20T20:30:00+00:00"),
      },
      {
        "name": "Sprint Qualifying",
        "startTime": datetime.fromisoformat("2025-03-20T23:30:00+00:00"),
        "endTime": datetime.fromisoformat("2025-03-21T00:30:00+00:00"),
      },
      {
        "name": "Sprint",
        "startTime": datetime.fromisoformat("2025-03-21T19:00:00+00:00"),
        "endTime": datetime.fromisoformat("2025-03-21T20:00:00+00:00"),
      },
      {
        "name": "Qualifying",
        "startTime": datetime.fromisoformat("2025-03-21T23:00:00+00:00"),
        "endTime": datetime.fromisoformat("2025-03-22T00:00:00+00:00"),
      },
      {
        "name": "Race",
        "startTime": datetime.fromisoformat("2025-03-22T22:00:00+00:00"),
        "endTime": datetime.fromisoformat("2025-03-23T00:00:00+00:00"),
      },
    ],
  },
  # Japan
  {
    "grandPrix": "Japanese Grand Prix",
    "circuit": "Suzuka International Racing Course",
    "country": "Japan",
    "hasSprint": False,
    "sessions": [
      {
        "name": "Free Practice 1",
        "startTime": datetime.fromisoformat("2025-04-04T02:30:00+00:00"),
        "endTime": datetime.fromisoformat("2025-04-04T03:30:00+00:00"),
      },
      {
        "name": "Free Practice 2",
        "startTime": datetime.fromisoformat("2025-04-04T06:00:00+00:00"),
        "endTime": datetime.fromisoformat("2025-04-04T07:00:00+00:00"),
      },
      {
        "name": "Free Practice 3",
        "startTime": datetime.fromisoformat("2025-04-05T02:30:00+00:00"),
        "endTime": datetime.fromisoformat("2025-04-05T03:30:00+00:00"),
      },
      {
        "name": "Qualifying",
        "startTime": datetime.fromisoformat("2025-04-05T06:00:00+00:00"),
        "endTime": datetime.fromisoformat("2025-04-05T07:00:00+00:00"),
      },
      {
        "name": "Race",
        "startTime": datetime.fromisoformat("2025-04-06T05:00:00+00:00"),
        "endTime": datetime.fromisoformat("2025-04-06T07:00:00+00:00"),
      },
    ],
  },
  # Bahrain
  {
    "grandPrix": "Bahrain Grand Prix",
    "circuit": "Bahrain International Circuit",
    "country": "Bahrain",
    "hasSprint": False,
    "sessions": [
      {
        "name": "Free Practice 1",
        "startTime": datetime.fromisoformat("2025-04-11T11:30:00+00:00"),
        "endTime": datetime.fromisoformat("2025-04-11T12:30:00+00:00"),
      },
      {
        "name": "Free Practice 2",
        "startTime": datetime.fromisoformat("2025-04-11T15:00:00+00:00"),
        "endTime": datetime.fromisoformat("2025-04-11T16:00:00+00:00"),
      },
      {
        "name": "Free Practice 3",
        "startTime": datetime.fromisoformat("2025-04-12T12:30:00+00:00"),
        "endTime": datetime.fromisoformat("2025-04-12T13:30:00+00:00"),
      },
      {
        "name": "Qualifying",
        "startTime": datetime.fromisoformat("2025-04-12T16:00:00+00:00"),
        "endTime": datetime.fromisoformat("2025-04-12T17:00:00+00:00"),
      },
      {
        "name": "Race",
        "startTime": datetime.fromisoformat("2025-04-13T15:00:00+00:00"),
        "endTime": datetime.fromisoformat("2025-04-13T17:00:00+00:00"),
      },
    ],
  },
  # Saudi Arabia
  {
    "grandPrix": "Saudi Arabian Grand Prix",
    "circuit": "Jeddah Corniche Circuit",
    "country": "Saudi Arabia",
    "hasSprint": False,
    "sessions": [
      {
        "name": "Free Practice 1",
        "startTime": datetime.fromisoformat("2025-04-18T13:30:00+00:00"),
        "endTime": datetime.fromisoformat("2025-04-18T14:30:00+00:00"),
      },
      {
        "name": "Free Practice 2",
        "startTime": datetime.fromisoformat("2025-04-18T17:00:00+00:00"),
        "endTime": datetime.fromisoformat("2025-04-18T18:00:00+00:00"),
      },
      {
        "name": "Free Practice 3",
        "startTime": datetime.fromisoformat("2025-04-19T13:30:00+00:00"),
        "endTime": datetime.fromisoformat("2025-04-19T14:30:00+00:00"),
      },
      {
        "name": "Qualifying",
        "startTime": datetime.fromisoformat("2025-04-19T17:00:00+00:00"),
        "endTime": datetime.fromisoformat("2025-04-19T18:00:00+00:00"),
      },
      {
        "name": "Race",
        "startTime": datetime.fromisoformat("2025-04-20T17:00:00+00:00"),
        "endTime": datetime.fromisoformat("2025-04-20T19:00:00+00:00"),
      },
    ],
  },
  # Miami
  {
    "grandPrix": "Miami Grand Prix",
    "circuit": "Miami International Autodrome",
    "country": "USA",
    "hasSprint": True,
    "sessions": [
      {
        "name": "Free Practice 1",
        "startTime": datetime.fromisoformat("2025-05-02T16:30:00+00:00"),
        "endTime": datetime.fromisoformat("2025-05-02T17:30:00+00:00"),
      },
      {
        "name": "Sprint Qualifying",
        "startTime": datetime.fromisoformat("2025-05-02T20:30:00+00:00"),
        "endTime": datetime.fromisoformat("2025-05-02T21:14:00+00:00"),
      },
      {
        "name": "Sprint",
        "startTime": datetime.fromisoformat("2025-05-03T16:00:00+00:00"),
        "endTime": datetime.fromisoformat("2025-05-03T17:00:00+00:00"),
      },
      {
        "name": "Qualifying",
        "startTime": datetime.fromisoformat("2025-05-03T20:00:00+00:00"),
        "endTime": datetime.fromisoformat("2025-05-03T21:00:00+00:00"),
      },
      {
        "name": "Race",
        "startTime": datetime.fromisoformat("2025-05-04T20:00:00+00:00"),
        "endTime": datetime.fromisoformat("2025-05-04T22:00:00+00:00"),
      },
    ],
  },
  # Imola
  {
    "grandPrix": "Emilia Romagna Grand Prix",
    "circuit": "Imola Circuit",
    "country": "Italy",
    "hasSprint": False,
    "sessions": [
      {
        "name": "Free Practice 1",
        "startTime": datetime.fromisoformat("2025-05-16T11:30:00+00:00"),
        "endTime": datetime.fromisoformat("2025-05-16T12:30:00+00:00"),
      },
      {
        "name": "Free Practice 2",
        "startTime": datetime.fromisoformat("2025-05-16T15:00:00+00:00"),
        "endTime": datetime.fromisoformat("2025-05-16T16:00:00+00:00"),
      },
      {
        "name": "Free Practice 3",
        "startTime": datetime.fromisoformat("2025-05-17T10:30:00+00:00"),
        "endTime": datetime.fromisoformat("2025-05-17T11:30:00+00:00"),
      },
      {
        "name": "Qualifying",
        "startTime": datetime.fromisoformat("2025-05-17T14:00:00+00:00"),
        "endTime": datetime.fromisoformat("2025-05-17T15:00:00+00:00"),
      },
      {
        "name": "Race",
        "startTime": datetime.fromisoformat("2025-05-18T13:00:00+00:00"),
        "endTime": datetime.fromisoformat("2025-05-18T15:00:00+00:00"),
      },
    ],
  },
  # Monaco
  {
    "grandPrix": "Monaco Grand Prix",
    "circuit": "Circuit de Monaco",
    "country": "Monaco",
    "hasSprint": False,
    "sessions": [
      {
        "name": "Free Practice 1",
        "startTime": datetime.fromisoformat("2025-05-23T11:30:00+00:00"),
        "endTime": datetime.fromisoformat("2025-05-23T12:30:00+00:00"),
      },
      {
        "name": "Free Practice 2",
        "startTime": datetime.fromisoformat("2025-05-23T15:00:00+00:00"),
        "endTime": datetime.fromisoformat("2025-05-23T16:00:00+00:00"),
      },
      {
        "name": "Free Practice 3",
        "startTime": datetime.fromisoformat("2025-05-24T10:30:00+00:00"),
        "endTime": datetime.fromisoformat("2025-05-24T11:30:00+00:00"),
      },
      {
        "name": "Qualifying",
        "startTime": datetime.fromisoformat("2025-05-24T14:00:00+00:00"),
        "endTime": datetime.fromisoformat("2025-05-24T15:00:00+00:00"),
      },
      {
        "name": "Race",
        "startTime": datetime.fromisoformat("2025-05-25T13:00:00+00:00"),
        "endTime": datetime.fromisoformat("2025-05-25T15:00:00+00:00"),
      },
    ],
  },
  # Spain
  {
    "grandPrix": "Spanish Grand Prix",
    "circuit": "Circuit de Barcelona-Catalunya",
    "country": "Spain",
    "hasSprint": False,
    "sessions": [
      {
        "name": "Free Practice 1",
        "startTime": datetime.fromisoformat("2025-05-30T11:30:00+00:00"),
        "endTime": datetime.fromisoformat("2025-05-30T12:30:00+00:00"),
      },
      {
        "name": "Free Practice 2",
        "startTime": datetime.fromisoformat("2025-05-30T15:00:00+00:00"),
        "endTime": datetime.fromisoformat("2025-05-30T16:00:00+00:00"),
      },
      {
        "name": "Free Practice 3",
        "startTime": datetime.fromisoformat("2025-05-31T10:30:00+00:00"),
        "endTime": datetime.fromisoformat("2025-05-31T11:30:00+00:00"),
      },
      {
        "name": "Qualifying",
        "startTime": datetime.fromisoformat("2025-05-31T14:00:00+00:00"),
        "endTime": datetime.fromisoformat("2025-05-31T15:00:00+00:00"),
      },
      {
        "name": "Race",
        "startTime": datetime.fromisoformat("2025-06-01T13:00:00+00:00"),
        "endTime": datetime.fromisoformat("2025-06-01T15:00:00+00:00"),
      },
    ],
  },
  # Canada
  {
    "grandPrix": "Canadian Grand Prix",
    "circuit": "Circuit Gilles-Villeneuve",
    "country": "Canada",
    "hasSprint": False,
    "sessions": [
      {
        "name": "Free Practice 1",
        "startTime": datetime.fromisoformat("2025-06-13T17:30:00+00:00"),
        "endTime": datetime.fromisoformat("2025-06-13T18:30:00+00:00"),
      },
      {
        "name": "Free Practice 2",
        "startTime": datetime.fromisoformat("2025-06-13T21:00:00+00:00"),
        "endTime": datetime.fromisoformat("2025-06-13T22:00:00+00:00"),
      },
      {
        "name": "Free Practice 3",
        "startTime": datetime.fromisoformat("2025-06-14T16:30:00+00:00"),
        "endTime": datetime.fromisoformat("2025-06-14T17:30:00+00:00"),
      },
      {
        "name": "Qualifying",
        "startTime": datetime.fromisoformat("2025-06-14T20:00:00+00:00"),
        "endTime": datetime.fromisoformat("2025-06-14T21:00:00+00:00"),
      },
      {
        "name": "Race",
        "startTime": datetime.fromisoformat("2025-06-15T18:00:00+00:00"),
        "endTime": datetime.fromisoformat("2025-06-15T20:00:00+00:00"),
      },
    ],
  },
  # Austria
  {
    "grandPrix": "Austrian Grand Prix",
    "circuit": "Red Bull Ring",
    "country": "Austria",
    "hasSprint": False,
    "sessions": [
      {
        "name": "Free Practice 1",
        "startTime": datetime.fromisoformat("2025-06-27T11:30:00+00:00"),
        "endTime": datetime.fromisoformat("2025-06-27T12:30:00+00:00"),
      },
      {
        "name": "Free Practice 2",
        "startTime": datetime.fromisoformat("2025-06-27T15:00:00+00:00"),
        "endTime": datetime.fromisoformat("2025-06-27T16:00:00+00:00"),
      },
      {
        "name": "Free Practice 3",
        "startTime": datetime.fromisoformat("2025-06-28T10:30:00+00:00"),
        "endTime": datetime.fromisoformat("2025-06-28T11:30:00+00:00"),
      },
      {
        "name": "Qualifying",
        "startTime": datetime.fromisoformat("2025-06-28T14:00:00+00:00"),
        "endTime": datetime.fromisoformat("2025-06-28T15:00:00+00:00"),
      },
      {
        "name": "Race",
        "startTime": datetime.fromisoformat("2025-06-29T13:00:00+00:00"),
        "endTime": datetime.fromisoformat("2025-06-29T15:00:00+00:00"),
      },
    ],
  },
  # Great Britain
  {
    "grandPrix": "British Grand Prix",
    "circuit": "Silverstone Circuit",
    "country": "United Kingdom",
    "hasSprint": False,
    "sessions": [
      {
        "name": "FP1",
        "startTime": datetime.fromisoformat("2025-07-04T11:30:00+00:00"),
        "endTime": datetime.fromisoformat("2025-07-04T12:30:00+00:00"),
      },
      {
        "name": "FP2",
        "startTime": datetime.fromisoformat("2025-07-04T15:00:00+00:00"),
        "endTime": datetime.fromisoformat("2025-07-04T16:00:00+00:00"),
      },
      {
        "name": "FP3",
        "startTime": datetime.fromisoformat("2025-07-05T10:30:00+00:00"),
        "endTime": datetime.fromisoformat("2025-07-05T11:30:00+00:00"),
      },
      {
        "name": "Qualifying",
        "startTime": datetime.fromisoformat("2025-07-05T14:00:00+00:00"),
        "endTime": datetime.fromisoformat("2025-07-05T15:00:00+00:00"),
      },
      {
        "name": "Race",
        "startTime": datetime.fromisoformat("2025-07-06T14:00:00+00:00"),
        "endTime": datetime.fromisoformat("2025-07-06T16:00:00+00:00"),
      },
    ],
  },
  # Belgium
  {
    "grandPrix": "Belgian Grand Prix",
    "circuit": "Spa-Francorchamps",
    "country": "Belgium",
    "hasSprint": True,
    "sessions": [
      {
        "name": "Free Practice 1",
        "startTime": datetime.fromisoformat("2025-07-25T10:30:00+00:00"),
        "endTime": datetime.fromisoformat("2025-07-25T11:30:00+00:00"),
      },
      {
        "name": "Sprint Qualifying",
        "startTime": datetime.fromisoformat("2025-07-25T14:30:00+00:00"),
        "endTime": datetime.fromisoformat("2025-07-25T15:30:00+00:00"),
      },
      {
        "name": "Sprint",
        "startTime": datetime.fromisoformat("2025-07-26T10:00:00+00:00"),
        "endTime": datetime.fromisoformat("2025-07-26T11:00:00+00:00"),
      },
      {
        "name": "Qualifying",
        "startTime": datetime.fromisoformat("2025-07-26T14:00:00+00:00"),
        "endTime": datetime.fromisoformat("2025-07-26T15:00:00+00:00"),
      },
      {
        "name": "Race",
        "startTime": datetime.fromisoformat("2025-07-27T13:00:00+00:00"),
        "endTime": datetime.fromisoformat("2025-07-27T15:00:00+00:00"),
      },
    ],
  },
  # Hungary
  {
    "grandPrix": "Hungarian Grand Prix",
    "circuit": "Hungaroring",
    "country": "Hungary",
    "hasSprint": False,
    "sessions": [
      {
        "name": "Free Practice 1",
        "startTime": datetime.fromisoformat("2025-08-01T11:30:00+00:00"),
        "endTime": datetime.fromisoformat("2025-08-01T12:30:00+00:00"),
      },
      {
        "name": "Free Practice 2",
        "startTime": datetime.fromisoformat("2025-08-01T15:00:00+00:00"),
        "endTime": datetime.fromisoformat("2025-08-01T16:00:00+00:00"),
      },
      {
        "name": "Free Practice 3",
        "startTime": datetime.fromisoformat("2025-08-02T10:30:00+00:00"),
        "endTime": datetime.fromisoformat("2025-08-02T11:30:00+00:00"),
      },
      {
        "name": "Qualifying",
        "startTime": datetime.fromisoformat("2025-08-02T14:00:00+00:00"),
        "endTime": datetime.fromisoformat("2025-08-02T15:00:00+00:00"),
      },
      {
        "name": "Race",
        "startTime": datetime.fromisoformat("2025-08-03T13:00:00+00:00"),
        "endTime": datetime.fromisoformat("2025-08-03T15:00:00+00:00"),
      },
    ],
  },
  # Netherlands
  {
    "grandPrix": "Dutch Grand Prix",
    "circuit": "Zandvoort Circuit",
    "country": "Netherlands",
    "hasSprint": False,
    "sessions": [
      {
        "name": "Free Practice 1",
        "startTime": datetime.fromisoformat("2025-08-29T10:30:00+00:00"),
        "endTime": datetime.fromisoformat("2025-08-29T11:30:00+00:00"),
      },
      {
        "name": "Free Practice 2",
        "startTime": datetime.fromisoformat("2025-08-29T14:00:00+00:00"),
        "endTime": datetime.fromisoformat("2025-08-29T15:00:00+00:00"),
      },
      {
        "name": "Free Practice 3",
        "startTime": datetime.fromisoformat("2025-08-30T09:30:00+00:00"),
        "endTime": datetime.fromisoformat("2025-08-30T10:30:00+00:00"),
      },
      {
        "name": "Qualifying",
        "startTime": datetime.fromisoformat("2025-08-30T13:00:00+00:00"),
        "endTime": datetime.fromisoformat("2025-08-30T14:00:00+00:00"),
      },
      {
        "name": "Race",
        "startTime": datetime.fromisoformat("2025-08-31T13:00:00+00:00"),
        "endTime": datetime.fromisoformat("2025-08-31T15:00:00+00:00"),
      },
    ],
  },
  # Italy
  {
    "grandPrix": "Italian Grand Prix",
    "circuit": "Monza Circuit",
    "country": "Italy",
    "hasSprint": False,
    "sessions": [
      {
        "name": "Free Practice 1",
        "startTime": datetime.fromisoformat("2025-09-05T11:30:00+00:00"),
        "endTime": datetime.fromisoformat("2025-09-05T12:30:00+00:00"),
      },
      {
        "name": "Free Practice 2",
        "startTime": datetime.fromisoformat("2025-09-05T15:00:00+00:00"),
        "endTime": datetime.fromisoformat("2025-09-05T16:00:00+00:00"),
      },
      {
        "name": "Free Practice 3",
        "startTime": datetime.fromisoformat("2025-09-06T10:30:00+00:00"),
        "endTime": datetime.fromisoformat("2025-09-06T11:30:00+00:00"),
      },
      {
        "name": "Qualifying",
        "startTime": datetime.fromisoformat("2025-09-06T14:00:00+00:00"),
        "endTime": datetime.fromisoformat("2025-09-06T15:00:00+00:00"),
      },
      {
        "name": "Race",
        "startTime": datetime.fromisoformat("2025-09-07T13:00:00+00:00"),
        "endTime": datetime.fromisoformat("2025-09-07T15:00:00+00:00"),
      },
    ],
  },
  # Azerbaijan
  {
    "grandPrix": "Azerbaijan Grand Prix",
    "circuit": "Baku City Circuit",
    "country": "Azerbaijan",
    "hasSprint": False,
    "sessions": [
      {
        "name": "Free Practice 1",
        "startTime": datetime.fromisoformat("2025-09-19T08:30:00+00:00"),
        "endTime": datetime.fromisoformat("2025-09-19T09:30:00+00:00"),
      },
      {
        "name": "Free Practice 2",
        "startTime": datetime.fromisoformat("2025-09-19T12:00:00+00:00"),
        "endTime": datetime.fromisoformat("2025-09-19T13:00:00+00:00"),
      },
      {
        "name": "Free Practice 3",
        "startTime": datetime.fromisoformat("2025-09-20T08:30:00+00:00"),
        "endTime": datetime.fromisoformat("2025-09-20T09:30:00+00:00"),
      },
      {
        "name": "Qualifying",
        "startTime": datetime.fromisoformat("2025-09-20T12:00:00+00:00"),
        "endTime": datetime.fromisoformat("2025-09-20T13:00:00+00:00"),
      },
      {
        "name": "Race",
        "startTime": datetime.fromisoformat("2025-09-21T11:00:00+00:00"),
        "endTime": datetime.fromisoformat("2025-09-21T13:00:00+00:00"),
      },
    ],
  },
  # Singapore
  {
    "grandPrix": "Singapore Grand Prix",
    "circuit": "Marina Bay Street Circuit",
    "country": "Singapore",
    "hasSprint": False,
    "sessions": [
      {
        "name": "Free Practice 1",
        "startTime": datetime.fromisoformat("2025-10-03T09:30:00+00:00"),
        "endTime": datetime.fromisoformat("2025-10-03T10:30:00+00:00"),
      },
      {
        "name": "Free Practice 2",
        "startTime": datetime.fromisoformat("2025-10-03T13:00:00+00:00"),
        "endTime": datetime.fromisoformat("2025-10-03T14:00:00+00:00"),
      },
      {
        "name": "Free Practice 3",
        "startTime": datetime.fromisoformat("2025-10-04T09:30:00+00:00"),
        "endTime": datetime.fromisoformat("2025-10-04T10:30:00+00:00"),
      },
      {
        "name": "Qualifying",
        "startTime": datetime.fromisoformat("2025-10-04T13:00:00+00:00"),
        "endTime": datetime.fromisoformat("2025-10-04T14:00:00+00:00"),
      },
      {
        "name": "Race",
        "startTime": datetime.fromisoformat("2025-10-05T12:00:00+00:00"),
        "endTime": datetime.fromisoformat("2025-10-05T14:00:00+00:00"),
      },
    ],
  },
  # United States
  {
    "grandPrix": "USA Grand Prix",
    "circuit": "Circuit of the Americas",
    "country": "USA",
    "hasSprint": True,
    "sessions": [
      {
        "name": "Free Practice 1",
        "startTime": datetime.fromisoformat("2025-10-17T17:30:00+00:00"),
        "endTime": datetime.fromisoformat("2025-10-17T18:30:00+00:00"),
      },
      {
        "name": "Sprint Qualifying",
        "startTime": datetime.fromisoformat("2025-10-17T21:30:00+00:00"),
        "endTime": datetime.fromisoformat("2025-10-17T22:14:00+00:00"),
      },
      {
        "name": "Sprint",
        "startTime": datetime.fromisoformat("2025-10-18T17:00:00+00:00"),
        "endTime": datetime.fromisoformat("2025-10-18T18:00:00+00:00"),
      },
      {
        "name": "Qualifying",
        "startTime": datetime.fromisoformat("2025-10-18T21:00:00+00:00"),
        "endTime": datetime.fromisoformat("2025-10-18T22:00:00+00:00"),
      },
      {
        "name": "Race",
        "startTime": datetime.fromisoformat("2025-10-19T19:00:00+00:00"),
        "endTime": datetime.fromisoformat("2025-10-19T21:00:00+00:00"),
      },
    ],
  },
  # Mexico
  {
    "grandPrix": "Mexico City Grand Prix",
    "circuit": "Autódromo Hermanos Rodríguez",
    "country": "Mexico",
    "hasSprint": False,
    "sessions": [
      {
        "name": "Free Practice 1",
        "startTime": datetime.fromisoformat("2025-10-24T18:30:00+00:00"),
        "endTime": datetime.fromisoformat("2025-10-24T19:30:00+00:00"),
      },
      {
        "name": "Free Practice 2",
        "startTime": datetime.fromisoformat("2025-10-24T22:00:00+00:00"),
        "endTime": datetime.fromisoformat("2025-10-24T23:00:00+00:00"),
      },
      {
        "name": "Free Practice 3",
        "startTime": datetime.fromisoformat("2025-10-25T18:30:00+00:00"),
        "endTime": datetime.fromisoformat("2025-10-25T18:30:00+00:00"),
      },
      {
        "name": "Qualifying",
        "startTime": datetime.fromisoformat("2025-10-25T21:00:00+00:00"),
        "endTime": datetime.fromisoformat("2025-10-25T22:00:00+00:00"),
      },
      {
        "name": "Race",
        "startTime": datetime.fromisoformat("2025-10-26T20:00:00+00:00"),
        "endTime": datetime.fromisoformat("2025-10-26T22:00:00+00:00"),
      },
    ],
  },
  # Brazil
  {
    "grandPrix": "São Paulo Grand Prix",
    "circuit": "Interlagos Circuit",
    "country": "Brazil",
    "hasSprint": True,
    "sessions": [
      {
        "name": "Free Practice 1",
        "startTime": datetime.fromisoformat("2025-11-07T14:30:00+00:00"),
        "endTime": datetime.fromisoformat("2025-11-07T15:30:00+00:00"),
      },
      {
        "name": "Sprint Qualifying",
        "startTime": datetime.fromisoformat("2025-11-07T18:30:00+00:00"),
        "endTime": datetime.fromisoformat("2025-11-07T19:14:00+00:00"),
      },
      {
        "name": "Sprint",
        "startTime": datetime.fromisoformat("2025-11-08T14:00:00+00:00"),
        "endTime": datetime.fromisoformat("2025-11-08T15:00:00+00:00"),
      },
      {
        "name": "Qualifying",
        "startTime": datetime.fromisoformat("2025-11-08T18:00:00+00:00"),
        "endTime": datetime.fromisoformat("2025-11-08T19:00:00+00:00"),
      },
      {
        "name": "Race",
        "startTime": datetime.fromisoformat("2025-11-09T17:00:00+00:00"),
        "endTime": datetime.fromisoformat("2025-11-09T19:00:00+00:00"),
      },
    ],
  },
  # Las Vegas
  {
    "grandPrix": "Las Vegas Grand Prix",
    "circuit": "Las Vegas Street Circuit",
    "country": "USA",
    "hasSprint": False,
    "sessions": [
      {
        "name": "Free Practice 1",
        "startTime": datetime.fromisoformat("2025-11-21T00:30:00+00:00"),
        "endTime": datetime.fromisoformat("2025-11-21T01:30:00+00:00"),
      },
      {
        "name": "Free Practice 2",
        "startTime": datetime.fromisoformat("2025-11-21T04:00:00+00:00"),
        "endTime": datetime.fromisoformat("2025-11-21T05:00:00+00:00"),
      },
      {
        "name": "Free Practice 3",
        "startTime": datetime.fromisoformat("2025-11-22T00:30:00+00:00"),
        "endTime": datetime.fromisoformat("2025-11-22T01:30:00+00:00"),
      },
      {
        "name": "Qualifying",
        "startTime": datetime.fromisoformat("2025-11-22T04:00:00+00:00"),
        "endTime": datetime.fromisoformat("2025-11-22T05:00:00+00:00"),
      },
      {
        "name": "Race",
        "startTime": datetime.fromisoformat("2025-11-23T04:00:00+00:00"),
        "endTime": datetime.fromisoformat("2025-11-23T06:00:00+00:00"),
      },
    ],
  },
  # Qatar
  {
    "grandPrix": "Qatar Grand Prix",
    "circuit": "Lusail International Circuit",
    "country": "Qatar",
    "hasSprint": True,
    "sessions": [
      {
        "name": "Free Practice 1",
        "startTime": datetime.fromisoformat("2025-11-28T13:30:00+00:00"),
        "endTime": datetime.fromisoformat("2025-11-28T14:30:00+00:00"),
      },
      {
        "name": "Sprint Qualifying",
        "startTime": datetime.fromisoformat("2025-11-28T17:30:00+00:00"),
        "endTime": datetime.fromisoformat("2025-11-28T18:14:00+00:00"),
      },
      {
        "name": "Sprint",
        "startTime": datetime.fromisoformat("2025-11-29T14:00:00+00:00"),
        "endTime": datetime.fromisoformat("2025-11-29T15:00:00+00:00"),
      },
      {
        "name": "Qualifying",
        "startTime": datetime.fromisoformat("2025-11-29T18:00:00+00:00"),
        "endTime": datetime.fromisoformat("2025-11-29T19:00:00+00:00"),
      },
      {
        "name": "Race",
        "startTime": datetime.fromisoformat("2025-11-30T16:00:00+00:00"),
        "endTime": datetime.fromisoformat("2025-11-30T18:00:00+00:00"),
      },
    ],
  },
  # Abu Dhabi
  {
    "grandPrix": "Abu Dhabi Grand Prix",
    "circuit": "Yas Marina Circuit",
    "country": "UAE",
    "hasSprint": False,
    "sessions": [
      {
        "name": "Free Practice 1",
        "startTime": datetime.fromisoformat("2025-12-05T09:30:00+00:00"),
        "endTime": datetime.fromisoformat("2025-12-05T10:30:00+00:00"),
      },
      {
        "name": "Free Practice 2",
        "startTime": datetime.fromisoformat("2025-12-05T13:00:00+00:00"),
        "endTime": datetime.fromisoformat("2025-12-05T14:00:00+00:00"),
      },
      {
        "name": "Free Practice 3",
        "startTime": datetime.fromisoformat("2025-12-06T10:30:00+00:00"),
        "endTime": datetime.fromisoformat("2025-12-06T11:30:00+00:00"),
      },
      {
        "name": "Qualifying",
        "startTime": datetime.fromisoformat("2025-12-06T14:00:00+00:00"),
        "endTime": datetime.fromisoformat("2025-12-06T15:00:00+00:00"),
      },
      {
        "name": "Race",
        "startTime": datetime.fromisoformat("2025-12-07T13:00:00+00:00"),
        "endTime": datetime.fromisoformat("2025-12-07T15:00:00+00:00"),
      },
    ],
  },
]

f1_2025_drivers_data =[
    {
        "number": 1,
        "name": "Max Verstappen",
        "team": "Red Bull Racing",
    },
    {
        "number": 44,
        "name": "Lewis Hamilton",
        "team": "Ferrari",
    },

]