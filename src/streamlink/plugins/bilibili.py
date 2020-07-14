import logging
import re
import time

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
        self.playlist_expire = None
        super(BilibiliHLSStreamWorker, self).__init__(*args, **kwargs)

    def reload_playlist(self):
        if not self.playlist_expire or self.playlist_expire - time.time() < 60 * 10:
            self.playlist_expire = time.time() + 60 * 60
            url = next(self.reader.stream.plugin.update_playlist())
            self.stream.args["url"] = url
        return super(BilibiliHLSStreamWorker, self).reload_playlist()

    def _set_playlist_reload_time(self, playlist, sequences):
        self.playlist_reload_time = 3

    def process_sequences(self, playlist, sequences):
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
        self.plugin = None

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
            _url = stream_list["url"]
            url = _url
            if "https://d1--cn-gotcha104.bilivideo.com" in url:
                url = url.replace("https://d1--cn-gotcha104.bilivideo.com", "http://3hq4yf8r2xgz9.cfc-execute.su.baidubce.com")

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
            yield url#.replace("https://", "hls://").replace("http://", "hls://")


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
        for url in self.update_playlist():
            #stream = BilibiliHLSStream.parse_variant_playlist(self.session, url)
            stream = BilibiliHLSStream(self.session, url)
            stream.plugin = self
            #for c in stream:
            #    stream[c].plugin = self
            yield name, stream
            #return stream


__plugin__ = Bilibili
