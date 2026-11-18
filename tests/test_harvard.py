import json
from unittest import TestCase
from unittest.mock import patch

from events_feed.harvard import seminar_cards


class SeminarDetailsTest(TestCase):
    @patch('events_feed.harvard.requests.get')
    def test_only_matching_detail_can_supply_missing_times(self, get):
        listing = b'<li class="tribe_events"><h3><a href="/epi/events/test/">Talk</a></h3><time datetime="2026-12-02"></time></li>'
        get.return_value.status_code = 200
        for day, expected in [('2026-12-02', ('13:00', '13:50')), ('2026-12-09', (None, None))]:
            event = {'@type': 'Event', 'name': 'Talk', 'startDate': day + 'T13:00:00-05:00',
                     'endDate': day + 'T13:50:00-05:00'}
            get.return_value.content = ('<script type="application/ld+json">' + json.dumps([event]) + '</script>').encode()
            card, = json.loads(seminar_cards(listing, 30))
            self.assertEqual((card['time'], card['end_time']), expected)
            get.assert_called_with('https://hsph.harvard.edu/epi/events/test/', timeout=30, allow_redirects=False)
