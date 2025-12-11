from datetime import datetime

# Times have been converted to UTC (Universal Time Coordinated).
f1_2026_races_data = [
  # Australia
  {
    "grandPrix": "Australian Grand Prix",
    "circuit": "Albert Park Circuit",
    "country": "Australia",
    "hasSprint": False,
    "sessions": [
      {
        "name": "Free Practice 1",
        "startTime": datetime.fromisoformat("2026-03-06T01:30:00+00:00"),
        "endTime": datetime.fromisoformat("2026-03-06T02:30:00+00:00"),
      },
      {
        "name": "Free Practice 2",
        "startTime": datetime.fromisoformat("2026-03-06T05:00:00+00:00"),
        "endTime": datetime.fromisoformat("2026-03-06T06:00:00+00:00"),
      },
      {
        "name": "Free Practice 3",
        "startTime": datetime.fromisoformat("2026-03-07T01:30:00+00:00"),
        "endTime": datetime.fromisoformat("2026-03-07T02:30:00+00:00"),
      },
      {
        "name": "Qualifying",
        "startTime": datetime.fromisoformat("2026-03-07T05:00:00+00:00"),
        "endTime": datetime.fromisoformat("2026-03-07T06:00:00+00:00"),
      },
      {
        "name": "Race",
        "startTime": datetime.fromisoformat("2026-03-08T04:00:00+00:00"),
        "endTime": datetime.fromisoformat("2026-03-08T06:00:00+00:00"),
      },
    ],
  },
  # China (Sprint)
  {
    "grandPrix": "Chinese Grand Prix",
    "circuit": "Shanghai International Circuit",
    "country": "China",
    "hasSprint": True,
    "sessions": [
      {
        "name": "Free Practice 1",
        "startTime": datetime.fromisoformat("2026-03-13T03:30:00+00:00"),
        "endTime": datetime.fromisoformat("2026-03-13T04:30:00+00:00"),
      },
      {
        "name": "Sprint Qualifying",
        "startTime": datetime.fromisoformat("2026-03-13T07:30:00+00:00"),
        "endTime": datetime.fromisoformat("2026-03-13T08:14:00+00:00"),
      },
      {
        "name": "Sprint",
        "startTime": datetime.fromisoformat("2026-03-14T03:00:00+00:00"),
        "endTime": datetime.fromisoformat("2026-03-14T04:00:00+00:00"),
      },
      {
        "name": "Qualifying",
        "startTime": datetime.fromisoformat("2026-03-14T07:00:00+00:00"),
        "endTime": datetime.fromisoformat("2026-03-14T08:00:00+00:00"),
      },
      {
        "name": "Race",
        "startTime": datetime.fromisoformat("2026-03-15T07:00:00+00:00"),
        "endTime": datetime.fromisoformat("2026-03-15T09:00:00+00:00"),
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
        "startTime": datetime.fromisoformat("2026-03-27T02:30:00+00:00"),
        "endTime": datetime.fromisoformat("2026-03-27T03:30:00+00:00"),
      },
      {
        "name": "Free Practice 2",
        "startTime": datetime.fromisoformat("2026-03-27T06:00:00+00:00"),
        "endTime": datetime.fromisoformat("2026-03-27T07:00:00+00:00"),
      },
      {
        "name": "Free Practice 3",
        "startTime": datetime.fromisoformat("2026-03-28T02:30:00+00:00"),
        "endTime": datetime.fromisoformat("2026-03-28T03:30:00+00:00"),
      },
      {
        "name": "Qualifying",
        "startTime": datetime.fromisoformat("2026-03-28T06:00:00+00:00"),
        "endTime": datetime.fromisoformat("2026-03-28T07:00:00+00:00"),
      },
      {
        "name": "Race",
        "startTime": datetime.fromisoformat("2026-03-29T05:00:00+00:00"),
        "endTime": datetime.fromisoformat("2026-03-29T07:00:00+00:00"),
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
        "startTime": datetime.fromisoformat("2026-04-10T11:30:00+00:00"),
        "endTime": datetime.fromisoformat("2026-04-10T12:30:00+00:00"),
      },
      {
        "name": "Free Practice 2",
        "startTime": datetime.fromisoformat("2026-04-10T15:00:00+00:00"),
        "endTime": datetime.fromisoformat("2026-04-10T16:00:00+00:00"),
      },
      {
        "name": "Free Practice 3",
        "startTime": datetime.fromisoformat("2026-04-11T12:30:00+00:00"),
        "endTime": datetime.fromisoformat("2026-04-11T13:30:00+00:00"),
      },
      {
        "name": "Qualifying",
        "startTime": datetime.fromisoformat("2026-04-11T16:00:00+00:00"),
        "endTime": datetime.fromisoformat("2026-04-11T17:00:00+00:00"),
      },
      {
        "name": "Race",
        "startTime": datetime.fromisoformat("2026-04-12T15:00:00+00:00"),
        "endTime": datetime.fromisoformat("2026-04-12T17:00:00+00:00"),
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
        "startTime": datetime.fromisoformat("2026-04-17T13:30:00+00:00"),
        "endTime": datetime.fromisoformat("2026-04-17T14:30:00+00:00"),
      },
      {
        "name": "Free Practice 2",
        "startTime": datetime.fromisoformat("2026-04-17T17:00:00+00:00"),
        "endTime": datetime.fromisoformat("2026-04-17T18:00:00+00:00"),
      },
      {
        "name": "Free Practice 3",
        "startTime": datetime.fromisoformat("2026-04-18T13:30:00+00:00"),
        "endTime": datetime.fromisoformat("2026-04-18T14:30:00+00:00"),
      },
      {
        "name": "Qualifying",
        "startTime": datetime.fromisoformat("2026-04-18T17:00:00+00:00"),
        "endTime": datetime.fromisoformat("2026-04-18T18:00:00+00:00"),
      },
      {
        "name": "Race",
        "startTime": datetime.fromisoformat("2026-04-19T17:00:00+00:00"),
        "endTime": datetime.fromisoformat("2026-04-19T19:00:00+00:00"),
      },
    ],
  },
  # Miami (Sprint)
  {
    "grandPrix": "Miami Grand Prix",
    "circuit": "Miami International Autodrome",
    "country": "USA",
    "hasSprint": True,
    "sessions": [
      {
        "name": "Free Practice 1",
        "startTime": datetime.fromisoformat("2026-05-01T16:30:00+00:00"),
        "endTime": datetime.fromisoformat("2026-05-01T17:30:00+00:00"),
      },
      {
        "name": "Sprint Qualifying",
        "startTime": datetime.fromisoformat("2026-05-01T20:30:00+00:00"),
        "endTime": datetime.fromisoformat("2026-05-01T21:14:00+00:00"),
      },
      {
        "name": "Sprint",
        "startTime": datetime.fromisoformat("2026-05-02T16:00:00+00:00"),
        "endTime": datetime.fromisoformat("2026-05-02T17:00:00+00:00"),
      },
      {
        "name": "Qualifying",
        "startTime": datetime.fromisoformat("2026-05-02T20:00:00+00:00"),
        "endTime": datetime.fromisoformat("2026-05-02T21:00:00+00:00"),
      },
      {
        "name": "Race",
        "startTime": datetime.fromisoformat("2026-05-03T20:00:00+00:00"),
        "endTime": datetime.fromisoformat("2026-05-03T22:00:00+00:00"),
      },
    ],
  },
  # Canada (Sprint - NEW)
  {
    "grandPrix": "Canadian Grand Prix",
    "circuit": "Circuit Gilles-Villeneuve",
    "country": "Canada",
    "hasSprint": True,
    "sessions": [
      {
        "name": "Free Practice 1",
        "startTime": datetime.fromisoformat("2026-05-22T16:30:00+00:00"),
        "endTime": datetime.fromisoformat("2026-05-22T17:30:00+00:00"),
      },
      {
        "name": "Sprint Qualifying",
        "startTime": datetime.fromisoformat("2026-05-22T20:30:00+00:00"),
        "endTime": datetime.fromisoformat("2026-05-22T21:14:00+00:00"),
      },
      {
        "name": "Sprint",
        "startTime": datetime.fromisoformat("2026-05-23T16:00:00+00:00"),
        "endTime": datetime.fromisoformat("2026-05-23T17:00:00+00:00"),
      },
      {
        "name": "Qualifying",
        "startTime": datetime.fromisoformat("2026-05-23T20:00:00+00:00"),
        "endTime": datetime.fromisoformat("2026-05-23T21:00:00+00:00"),
      },
      {
        "name": "Race",
        "startTime": datetime.fromisoformat("2026-05-24T20:00:00+00:00"),
        "endTime": datetime.fromisoformat("2026-05-24T22:00:00+00:00"),
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
        "startTime": datetime.fromisoformat("2026-06-05T11:30:00+00:00"),
        "endTime": datetime.fromisoformat("2026-06-05T12:30:00+00:00"),
      },
      {
        "name": "Free Practice 2",
        "startTime": datetime.fromisoformat("2026-06-05T15:00:00+00:00"),
        "endTime": datetime.fromisoformat("2026-06-05T16:00:00+00:00"),
      },
      {
        "name": "Free Practice 3",
        "startTime": datetime.fromisoformat("2026-06-06T10:30:00+00:00"),
        "endTime": datetime.fromisoformat("2026-06-06T11:30:00+00:00"),
      },
      {
        "name": "Qualifying",
        "startTime": datetime.fromisoformat("2026-06-06T14:00:00+00:00"),
        "endTime": datetime.fromisoformat("2026-06-06T15:00:00+00:00"),
      },
      {
        "name": "Race",
        "startTime": datetime.fromisoformat("2026-06-07T13:00:00+00:00"),
        "endTime": datetime.fromisoformat("2026-06-07T15:00:00+00:00"),
      },
    ],
  },
  # Barcelona-Catalunya
  {
    "grandPrix": "Gran Premio de Barcelona-Catalunya",
    "circuit": "Circuit de Barcelona-Catalunya",
    "country": "Spain",
    "hasSprint": False,
    "sessions": [
      {
        "name": "Free Practice 1",
        "startTime": datetime.fromisoformat("2026-06-12T11:30:00+00:00"),
        "endTime": datetime.fromisoformat("2026-06-12T12:30:00+00:00"),
      },
      {
        "name": "Free Practice 2",
        "startTime": datetime.fromisoformat("2026-06-12T15:00:00+00:00"),
        "endTime": datetime.fromisoformat("2026-06-12T16:00:00+00:00"),
      },
      {
        "name": "Free Practice 3",
        "startTime": datetime.fromisoformat("2026-06-13T10:30:00+00:00"),
        "endTime": datetime.fromisoformat("2026-06-13T11:30:00+00:00"),
      },
      {
        "name": "Qualifying",
        "startTime": datetime.fromisoformat("2026-06-13T14:00:00+00:00"),
        "endTime": datetime.fromisoformat("2026-06-13T15:00:00+00:00"),
      },
      {
        "name": "Race",
        "startTime": datetime.fromisoformat("2026-06-14T13:00:00+00:00"),
        "endTime": datetime.fromisoformat("2026-06-14T15:00:00+00:00"),
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
        "startTime": datetime.fromisoformat("2026-06-26T11:30:00+00:00"),
        "endTime": datetime.fromisoformat("2026-06-26T12:30:00+00:00"),
      },
      {
        "name": "Free Practice 2",
        "startTime": datetime.fromisoformat("2026-06-26T15:00:00+00:00"),
        "endTime": datetime.fromisoformat("2026-06-26T16:00:00+00:00"),
      },
      {
        "name": "Free Practice 3",
        "startTime": datetime.fromisoformat("2026-06-27T10:30:00+00:00"),
        "endTime": datetime.fromisoformat("2026-06-27T11:30:00+00:00"),
      },
      {
        "name": "Qualifying",
        "startTime": datetime.fromisoformat("2026-06-27T14:00:00+00:00"),
        "endTime": datetime.fromisoformat("2026-06-27T15:00:00+00:00"),
      },
      {
        "name": "Race",
        "startTime": datetime.fromisoformat("2026-06-28T13:00:00+00:00"),
        "endTime": datetime.fromisoformat("2026-06-28T15:00:00+00:00"),
      },
    ],
  },
  # Great Britain (Sprint)
  {
    "grandPrix": "British Grand Prix",
    "circuit": "Silverstone Circuit",
    "country": "United Kingdom",
    "hasSprint": True,
    "sessions": [
      {
        "name": "Free Practice 1",
        "startTime": datetime.fromisoformat("2026-07-03T11:30:00+00:00"),
        "endTime": datetime.fromisoformat("2026-07-03T12:30:00+00:00"),
      },
      {
        "name": "Sprint Qualifying",
        "startTime": datetime.fromisoformat("2026-07-03T15:30:00+00:00"),
        "endTime": datetime.fromisoformat("2026-07-03T16:14:00+00:00"),
      },
      {
        "name": "Sprint",
        "startTime": datetime.fromisoformat("2026-07-04T11:00:00+00:00"),
        "endTime": datetime.fromisoformat("2026-07-04T12:00:00+00:00"),
      },
      {
        "name": "Qualifying",
        "startTime": datetime.fromisoformat("2026-07-04T15:00:00+00:00"),
        "endTime": datetime.fromisoformat("2026-07-04T16:00:00+00:00"),
      },
      {
        "name": "Race",
        "startTime": datetime.fromisoformat("2026-07-05T14:00:00+00:00"),
        "endTime": datetime.fromisoformat("2026-07-05T16:00:00+00:00"),
      },
    ],
  },
  # Belgium
  {
    "grandPrix": "Belgian Grand Prix",
    "circuit": "Spa-Francorchamps",
    "country": "Belgium",
    "hasSprint": False,
    "sessions": [
      {
        "name": "Free Practice 1",
        "startTime": datetime.fromisoformat("2026-07-17T11:30:00+00:00"),
        "endTime": datetime.fromisoformat("2026-07-17T12:30:00+00:00"),
      },
      {
        "name": "Free Practice 2",
        "startTime": datetime.fromisoformat("2026-07-17T15:00:00+00:00"),
        "endTime": datetime.fromisoformat("2026-07-17T16:00:00+00:00"),
      },
      {
        "name": "Free Practice 3",
        "startTime": datetime.fromisoformat("2026-07-18T10:30:00+00:00"),
        "endTime": datetime.fromisoformat("2026-07-18T11:30:00+00:00"),
      },
      {
        "name": "Qualifying",
        "startTime": datetime.fromisoformat("2026-07-18T14:00:00+00:00"),
        "endTime": datetime.fromisoformat("2026-07-18T15:00:00+00:00"),
      },
      {
        "name": "Race",
        "startTime": datetime.fromisoformat("2026-07-19T13:00:00+00:00"),
        "endTime": datetime.fromisoformat("2026-07-19T15:00:00+00:00"),
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
        "startTime": datetime.fromisoformat("2026-07-24T11:30:00+00:00"),
        "endTime": datetime.fromisoformat("2026-07-24T12:30:00+00:00"),
      },
      {
        "name": "Free Practice 2",
        "startTime": datetime.fromisoformat("2026-07-24T15:00:00+00:00"),
        "endTime": datetime.fromisoformat("2026-07-24T16:00:00+00:00"),
      },
      {
        "name": "Free Practice 3",
        "startTime": datetime.fromisoformat("2026-07-25T10:30:00+00:00"),
        "endTime": datetime.fromisoformat("2026-07-25T11:30:00+00:00"),
      },
      {
        "name": "Qualifying",
        "startTime": datetime.fromisoformat("2026-07-25T14:00:00+00:00"),
        "endTime": datetime.fromisoformat("2026-07-25T15:00:00+00:00"),
      },
      {
        "name": "Race",
        "startTime": datetime.fromisoformat("2026-07-26T13:00:00+00:00"),
        "endTime": datetime.fromisoformat("2026-07-26T15:00:00+00:00"),
      },
    ],
  },
  # Netherlands (Sprint - NEW)
  {
    "grandPrix": "Dutch Grand Prix",
    "circuit": "Zandvoort Circuit",
    "country": "Netherlands",
    "hasSprint": True,
    "sessions": [
      {
        "name": "Free Practice 1",
        "startTime": datetime.fromisoformat("2026-08-21T10:30:00+00:00"),
        "endTime": datetime.fromisoformat("2026-08-21T11:30:00+00:00"),
      },
      {
        "name": "Sprint Qualifying",
        "startTime": datetime.fromisoformat("2026-08-21T14:30:00+00:00"),
        "endTime": datetime.fromisoformat("2026-08-21T15:14:00+00:00"),
      },
      {
        "name": "Sprint",
        "startTime": datetime.fromisoformat("2026-08-22T10:00:00+00:00"),
        "endTime": datetime.fromisoformat("2026-08-22T11:00:00+00:00"),
      },
      {
        "name": "Qualifying",
        "startTime": datetime.fromisoformat("2026-08-22T14:00:00+00:00"),
        "endTime": datetime.fromisoformat("2026-08-22T15:00:00+00:00"),
      },
      {
        "name": "Race",
        "startTime": datetime.fromisoformat("2026-08-23T13:00:00+00:00"),
        "endTime": datetime.fromisoformat("2026-08-23T15:00:00+00:00"),
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
        "startTime": datetime.fromisoformat("2026-09-04T10:30:00+00:00"),
        "endTime": datetime.fromisoformat("2026-09-04T11:30:00+00:00"),
      },
      {
        "name": "Free Practice 2",
        "startTime": datetime.fromisoformat("2026-09-04T14:00:00+00:00"),
        "endTime": datetime.fromisoformat("2026-09-04T15:00:00+00:00"),
      },
      {
        "name": "Free Practice 3",
        "startTime": datetime.fromisoformat("2026-09-05T10:30:00+00:00"),
        "endTime": datetime.fromisoformat("2026-09-05T11:30:00+00:00"),
      },
      {
        "name": "Qualifying",
        "startTime": datetime.fromisoformat("2026-09-05T14:00:00+00:00"),
        "endTime": datetime.fromisoformat("2026-09-05T15:00:00+00:00"),
      },
      {
        "name": "Race",
        "startTime": datetime.fromisoformat("2026-09-06T13:00:00+00:00"),
        "endTime": datetime.fromisoformat("2026-09-06T15:00:00+00:00"),
      },
    ],
  },
  # Spain (Madrid - NEW)
  {
    "grandPrix": "Spanish Grand Prix",
    "circuit": "Madring",
    "country": "Spain",
    "hasSprint": False,
    "sessions": [
      {
        "name": "Free Practice 1",
        "startTime": datetime.fromisoformat("2026-09-11T11:30:00+00:00"),
        "endTime": datetime.fromisoformat("2026-09-11T12:30:00+00:00"),
      },
      {
        "name": "Free Practice 2",
        "startTime": datetime.fromisoformat("2026-09-11T15:00:00+00:00"),
        "endTime": datetime.fromisoformat("2026-09-11T16:00:00+00:00"),
      },
      {
        "name": "Free Practice 3",
        "startTime": datetime.fromisoformat("2026-09-12T10:30:00+00:00"),
        "endTime": datetime.fromisoformat("2026-09-12T11:30:00+00:00"),
      },
      {
        "name": "Qualifying",
        "startTime": datetime.fromisoformat("2026-09-12T14:00:00+00:00"),
        "endTime": datetime.fromisoformat("2026-09-12T15:00:00+00:00"),
      },
      {
        "name": "Race",
        "startTime": datetime.fromisoformat("2026-09-13T13:00:00+00:00"),
        "endTime": datetime.fromisoformat("2026-09-13T15:00:00+00:00"),
      },
    ],
  },
  # Azerbaijan (Saturday Race)
  {
    "grandPrix": "Azerbaijan Grand Prix",
    "circuit": "Baku City Circuit",
    "country": "Azerbaijan",
    "hasSprint": False,
    "sessions": [
      {
        "name": "Free Practice 1",
        "startTime": datetime.fromisoformat("2026-09-24T08:30:00+00:00"),
        "endTime": datetime.fromisoformat("2026-09-24T09:30:00+00:00"),
      },
      {
        "name": "Free Practice 2",
        "startTime": datetime.fromisoformat("2026-09-24T12:00:00+00:00"),
        "endTime": datetime.fromisoformat("2026-09-24T13:00:00+00:00"),
      },
      {
        "name": "Free Practice 3",
        "startTime": datetime.fromisoformat("2026-09-25T08:30:00+00:00"),
        "endTime": datetime.fromisoformat("2026-09-25T09:30:00+00:00"),
      },
      {
        "name": "Qualifying",
        "startTime": datetime.fromisoformat("2026-09-25T12:00:00+00:00"),
        "endTime": datetime.fromisoformat("2026-09-25T13:00:00+00:00"),
      },
      {
        "name": "Race",
        "startTime": datetime.fromisoformat("2026-09-26T11:00:00+00:00"),
        "endTime": datetime.fromisoformat("2026-09-26T13:00:00+00:00"),
      },
    ],
  },
  # Singapore (Sprint - NEW)
  {
    "grandPrix": "Singapore Grand Prix",
    "circuit": "Marina Bay Street Circuit",
    "country": "Singapore",
    "hasSprint": True,
    "sessions": [
      {
        "name": "Free Practice 1",
        "startTime": datetime.fromisoformat("2026-10-09T09:30:00+00:00"),
        "endTime": datetime.fromisoformat("2026-10-09T10:30:00+00:00"),
      },
      {
        "name": "Sprint Qualifying",
        "startTime": datetime.fromisoformat("2026-10-09T13:30:00+00:00"),
        "endTime": datetime.fromisoformat("2026-10-09T14:14:00+00:00"),
      },
      {
        "name": "Sprint",
        "startTime": datetime.fromisoformat("2026-10-10T09:00:00+00:00"),
        "endTime": datetime.fromisoformat("2026-10-10T10:00:00+00:00"),
      },
      {
        "name": "Qualifying",
        "startTime": datetime.fromisoformat("2026-10-10T13:00:00+00:00"),
        "endTime": datetime.fromisoformat("2026-10-10T14:00:00+00:00"),
      },
      {
        "name": "Race",
        "startTime": datetime.fromisoformat("2026-10-11T12:00:00+00:00"),
        "endTime": datetime.fromisoformat("2026-10-11T14:00:00+00:00"),
      },
    ],
  },
  # United States
  {
    "grandPrix": "USA Grand Prix",
    "circuit": "Circuit of the Americas",
    "country": "USA",
    "hasSprint": False,
    "sessions": [
      {
        "name": "Free Practice 1",
        "startTime": datetime.fromisoformat("2026-10-23T17:30:00+00:00"),
        "endTime": datetime.fromisoformat("2026-10-23T18:30:00+00:00"),
      },
      {
        "name": "Free Practice 2",
        "startTime": datetime.fromisoformat("2026-10-23T21:00:00+00:00"),
        "endTime": datetime.fromisoformat("2026-10-23T22:00:00+00:00"),
      },
      {
        "name": "Free Practice 3",
        "startTime": datetime.fromisoformat("2026-10-24T17:00:00+00:00"),
        "endTime": datetime.fromisoformat("2026-10-24T18:00:00+00:00"),
      },
      {
        "name": "Qualifying",
        "startTime": datetime.fromisoformat("2026-10-24T21:00:00+00:00"),
        "endTime": datetime.fromisoformat("2026-10-24T22:00:00+00:00"),
      },
      {
        "name": "Race",
        "startTime": datetime.fromisoformat("2026-10-25T20:00:00+00:00"),
        "endTime": datetime.fromisoformat("2026-10-25T22:00:00+00:00"),
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
        "startTime": datetime.fromisoformat("2026-10-30T18:30:00+00:00"),
        "endTime": datetime.fromisoformat("2026-10-30T19:30:00+00:00"),
      },
      {
        "name": "Free Practice 2",
        "startTime": datetime.fromisoformat("2026-10-30T22:00:00+00:00"),
        "endTime": datetime.fromisoformat("2026-10-30T23:00:00+00:00"),
      },
      {
        "name": "Free Practice 3",
        "startTime": datetime.fromisoformat("2026-10-31T17:30:00+00:00"),
        "endTime": datetime.fromisoformat("2026-10-31T18:30:00+00:00"),
      },
      {
        "name": "Qualifying",
        "startTime": datetime.fromisoformat("2026-10-31T21:00:00+00:00"),
        "endTime": datetime.fromisoformat("2026-10-31T22:00:00+00:00"),
      },
      {
        "name": "Race",
        "startTime": datetime.fromisoformat("2026-11-01T20:00:00+00:00"),
        "endTime": datetime.fromisoformat("2026-11-01T22:00:00+00:00"),
      },
    ],
  },
  # Brazil
  {
    "grandPrix": "São Paulo Grand Prix",
    "circuit": "Interlagos Circuit",
    "country": "Brazil",
    "hasSprint": False,
    "sessions": [
      {
        "name": "Free Practice 1",
        "startTime": datetime.fromisoformat("2026-11-06T15:30:00+00:00"),
        "endTime": datetime.fromisoformat("2026-11-06T16:30:00+00:00"),
      },
      {
        "name": "Free Practice 2",
        "startTime": datetime.fromisoformat("2026-11-06T19:00:00+00:00"),
        "endTime": datetime.fromisoformat("2026-11-06T20:00:00+00:00"),
      },
      {
        "name": "Free Practice 3",
        "startTime": datetime.fromisoformat("2026-11-07T14:30:00+00:00"),
        "endTime": datetime.fromisoformat("2026-11-07T15:30:00+00:00"),
      },
      {
        "name": "Qualifying",
        "startTime": datetime.fromisoformat("2026-11-07T18:00:00+00:00"),
        "endTime": datetime.fromisoformat("2026-11-07T19:00:00+00:00"),
      },
      {
        "name": "Race",
        "startTime": datetime.fromisoformat("2026-11-08T17:00:00+00:00"),
        "endTime": datetime.fromisoformat("2026-11-08T19:00:00+00:00"),
      },
    ],
  },
  # Las Vegas (Saturday Race)
  {
    "grandPrix": "Las Vegas Grand Prix",
    "circuit": "Las Vegas Street Circuit",
    "country": "USA",
    "hasSprint": False,
    "sessions": [
      {
        "name": "Free Practice 1",
        "startTime": datetime.fromisoformat("2026-11-20T00:30:00+00:00"),
        "endTime": datetime.fromisoformat("2026-11-20T01:30:00+00:00"),
      },
      {
        "name": "Free Practice 2",
        "startTime": datetime.fromisoformat("2026-11-20T04:00:00+00:00"),
        "endTime": datetime.fromisoformat("2026-11-20T05:00:00+00:00"),
      },
      {
        "name": "Free Practice 3",
        "startTime": datetime.fromisoformat("2026-11-21T00:30:00+00:00"),
        "endTime": datetime.fromisoformat("2026-11-21T01:30:00+00:00"),
      },
      {
        "name": "Qualifying",
        "startTime": datetime.fromisoformat("2026-11-21T04:00:00+00:00"),
        "endTime": datetime.fromisoformat("2026-11-21T05:00:00+00:00"),
      },
      {
        "name": "Race",
        "startTime": datetime.fromisoformat("2026-11-22T03:00:00+00:00"),
        "endTime": datetime.fromisoformat("2026-11-22T05:00:00+00:00"),
      },
    ],
  },
  # Qatar
  {
    "grandPrix": "Qatar Grand Prix",
    "circuit": "Lusail International Circuit",
    "country": "Qatar",
    "hasSprint": False,
    "sessions": [
      {
        "name": "Free Practice 1",
        "startTime": datetime.fromisoformat("2026-11-27T13:30:00+00:00"),
        "endTime": datetime.fromisoformat("2026-11-27T14:30:00+00:00"),
      },
      {
        "name": "Free Practice 2",
        "startTime": datetime.fromisoformat("2026-11-27T17:00:00+00:00"),
        "endTime": datetime.fromisoformat("2026-11-27T18:00:00+00:00"),
      },
      {
        "name": "Free Practice 3",
        "startTime": datetime.fromisoformat("2026-11-28T14:30:00+00:00"),
        "endTime": datetime.fromisoformat("2026-11-28T15:30:00+00:00"),
      },
      {
        "name": "Qualifying",
        "startTime": datetime.fromisoformat("2026-11-28T18:00:00+00:00"),
        "endTime": datetime.fromisoformat("2026-11-28T19:00:00+00:00"),
      },
      {
        "name": "Race",
        "startTime": datetime.fromisoformat("2026-11-29T17:00:00+00:00"),
        "endTime": datetime.fromisoformat("2026-11-29T19:00:00+00:00"),
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
        "startTime": datetime.fromisoformat("2026-12-04T09:30:00+00:00"),
        "endTime": datetime.fromisoformat("2026-12-04T10:30:00+00:00"),
      },
      {
        "name": "Free Practice 2",
        "startTime": datetime.fromisoformat("2026-12-04T13:00:00+00:00"),
        "endTime": datetime.fromisoformat("2026-12-04T14:00:00+00:00"),
      },
      {
        "name": "Free Practice 3",
        "startTime": datetime.fromisoformat("2026-12-05T10:30:00+00:00"),
        "endTime": datetime.fromisoformat("2026-12-05T11:30:00+00:00"),
      },
      {
        "name": "Qualifying",
        "startTime": datetime.fromisoformat("2026-12-05T14:00:00+00:00"),
        "endTime": datetime.fromisoformat("2026-12-05T15:00:00+00:00"),
      },
      {
        "name": "Race",
        "startTime": datetime.fromisoformat("2026-12-06T13:00:00+00:00"),
        "endTime": datetime.fromisoformat("2026-12-06T15:00:00+00:00"),
      },
    ],
  },
]