import logging
import re

from streamlink.compat import urlparse
from streamlink.plugin import Plugin, PluginArguments, PluginArgument
from streamlink.plugin.api import validate, useragents
from streamlink.stream import HTTPStream
from streamlink.stream import (
    HTTPStream, HLSStream, FLVPlaylist, extract_flv_header_tags
)
from streamlink.stream.hls import HLSStreamReader, HLSStreamWriter, HLSStreamWorker
from streamlink.stream.hls_playlist import M3U8Parser, load as load_hls_playlist
from streamlink.utils.times import hours_minutes_seconds


log = logging.getLogger(__name__)

API_HOST = "https://api.live.bilibili.com"
API_URL = "/room/v1/Room/playUrl"
MAPI_URL =  "/xlive/web-room/v1/playUrl/playUrl"
ROOM_API = "/room/v1/Room/room_init?id={}"
SHOW_STATUS_OFFLINE = 0
SHOW_STATUS_ONLINE = 1
SHOW_STATUS_ROUND = 2
STREAM_WEIGHTS = {
    "source": 1080
}

_url_re = re.compile(r"""
    http(s)?://live.bilibili.com
    /(?P<channel>[^/]+)
""", re.VERBOSE)

_room_id_schema = validate.Schema(
    {
        "data": validate.any(None, {
            "room_id": int,
            "live_status": int
        })
    },
    validate.get("data")
)

_room_stream_list_schema = validate.Schema(
    {
        "data": validate.any(None, {
            "durl": [{"url": validate.url()}]
        })
    },
    validate.get("data")
)


class BilibiliHLSStreamWorker(HLSStreamWorker):
    def __init__(self, *args, **kwargs):
        self.parent = None
        self.playlist_reloads = 0
        super(BilibiliHLSStreamWorker, self).__init__(*args, **kwargs)

    def _reload_playlist(self, text, url):
        url = self.parent.update_playlist()
        self.playlist_reloads += 1
        playlist = load_hls_playlist(
            text,
            url,
        )
        if (
            self.stream.disable_ads
            and self.playlist_reloads == 1
            and not next((s for s in playlist.segments if not s.ad), False)
        ):
            log.info("Waiting for pre-roll ads to finish, be patient")

        return playlist

    def _set_playlist_reload_time(self, playlist, sequences):
        if self.stream.low_latency and len(sequences) > 0:
            self.playlist_reload_time = sequences[-1].segment.duration
        else:
            super(BilibiliHLSStreamWorker, self)._set_playlist_reload_time(playlist, sequences)

    def process_sequences(self, playlist, sequences):
        if self.stream.low_latency and self.playlist_reloads == 1 and not playlist.has_prefetch_segments:
            log.info("This is not a low latency stream")

        return super(BilibiliHLSStreamWorker, self).process_sequences(playlist, sequences)


class BilibiliHLSStreamWriter(HLSStreamWriter):
    def write(self, sequence, *args, **kwargs):
        return super(BilibiliHLSStreamWriter, self).write(sequence, *args, **kwargs)


class BilibiliHLSStreamReader(HLSStreamReader):
    __worker__ = BilibiliHLSStreamWorker
    __writer__ = BilibiliHLSStreamWriter


LOW_LATENCY_MAX_LIVE_EDGE = 2

class BilibiliHLSStream(HLSStream):
    def __init__(self, *args, **kwargs):
        super(BilibiliHLSStream, self).__init__(*args, **kwargs)

    def open(self):
        reader = BilibiliHLSStreamReader(self)
        reader.open()
        return reader

    @classmethod
    def _get_variant_playlist(cls, res):
        return load_hls_playlist(res.text, base_uri=res.url)



class Bilibili(Plugin):
    arguments = PluginArguments(
        PluginArgument(
            "apihost",
            metavar="APIHOST",
            default=API_HOST,
            help="Use custom api host url to bypass bilibili's cloud blocking"
        )
    )

    @classmethod
    def can_handle_url(self, url):
        return _url_re.match(url)

    @classmethod
    def stream_weight(cls, stream):
        if stream in STREAM_WEIGHTS:
            return STREAM_WEIGHTS[stream], "Bilibili"

        return Plugin.stream_weight(stream)

    def update_playlist(self):
        params = {
            'cid': self.room_id,
            'qn': '4',
            'platform': 'h5',
        }
        res = self.session.http.get(self.options.get("apihost") + API_URL, params=params)
        room = self.session.http.json(res, schema=_room_stream_list_schema)
        if not room:
            return

        for stream_list in room["durl"]:
            url = stream_list["url"]
            # check if the URL is available
            log.trace('URL={0}'.format(url))
            r = self.session.http.get(url,
                                      retries=0,
                                      timeout=3,
                                      stream=True,
                                      acceptable_status=(200, 403, 404, 405))
            p = urlparse(url)
            if r.status_code != 200:
                log.error('Netloc: {0} with error {1}'.format(p.netloc, r.status_code))
                continue
            log.debug('Netloc: {0}'.format(p.netloc))
            return url


    def _get_streams(self):
        self.session.http.headers.update({
            'User-Agent': useragents.FIREFOX,
            'Referer': self.url})
        match = _url_re.match(self.url)
        channel = match.group("channel")
        res_room_id = self.session.http.get(self.options.get("apihost") + ROOM_API.format(channel))
        room_id_json = self.session.http.json(res_room_id, schema=_room_id_schema)
        self.room_id = room_id_json['room_id']
        if room_id_json['live_status'] != SHOW_STATUS_ONLINE:
            return

        '''params = {
            'cid': room_id,
            'quality': '4',
            'platform': 'web',
        }'''
        name = "source"
        url = self.update_playlist()
        stream = BilibiliHLSStream.parse_variant_playlist(self.session, url)
        stream.parent = self
        yield name, stream


__plugin__ = Bilibili
