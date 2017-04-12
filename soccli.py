import json
import math
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta
from operator import itemgetter
from urllib import request

AUDIO_FILE_PLAY_CMD = 'afplay -v 0.7 '
DL_FNAME = '/tmp/scpfile'
ESC_BOLD = '\033[1m'
ESC_CYAN = '\033[36m'
ESC_OFF = '\033[0m'
ESC_YELLOW = '\033[33m'
GREETING_MESSAGE = 'Please type a search term or \'x\' to exit...'
SOUNDCLOUD_DATEFORMAT = "%Y/%m/%d %H:%M:%S %z"
SOUNDCLOUD_TRACKS_BASE_URL = 'http://api.soundcloud.com/tracks.json?'
VLC_PATH = '/Applications/VLC.app/Contents/MacOS/VLC'


class Player():
    # Player searches and plays music.
    #
    # I'm a lumberjack, and I'm okay.
    # I sleep all night and I work all day.
    def __init__(self):
        self.minD = 50 * 60 * 1000
        self.maxD = 500 * 60 * 1000
        self.last_input = ''
        self.result = []
        script_folder = os.path.dirname(os.path.realpath(__file__))
        try:
            with open(script_folder + "/client_id.txt") as file:
                self.client_id = file.read()
        except FileNotFoundError:
            print(
                'Please place a file called'
                ' "client_id.txt" in the folder of your script. \n'
                'It should only contain your soundcloud api key.'
            )
            sys.exit()

    def _set(self):
        trimmed = self.last_input[4:]
        setting = trimmed.split()
        if setting[0] == 'range':
            self._set_min_d(int(setting[1]))
            self._set_max_d(int(setting[2]))

    def _set_min_d(self, v):
        self.minD = v * 60_000

    def _set_max_d(self, v):
        self.maxD = v * 60_000

    def _search(self):
        q = self.last_input.replace(' ', '+')
        print('Searching', q, '\n')

        params = {
            'client_id': self.client_id,
            'q': q,
            'duration_from': self.minD,
            'duration_to': self.maxD
        }
        uri = (
            SOUNDCLOUD_TRACKS_BASE_URL +
            "client_id={client_id}&q={q}"
            "&duration[from]={duration_from}"
            "&duration[to]={duration_to}"
            "&filter=streamable,public"
        ).format(**params)

        track_keys = [
            'title',
            'created_at',
            'duration',
            'stream_url',
            'description',
            'permalink_url',
            'download_url',
            'user',
            'downloadable'
        ]

        with request.urlopen(uri) as resp:
            r = json.loads(resp.read().decode('utf-8'))
            sorted_result = sorted(r, key=itemgetter('created_at'))
            self.result = []
            for row in sorted_result:
                self.result.append({k: row[k] for k in track_keys})
            print('... end of search')

    def _show_results(self):
        result_line_tpl = (
            '{rank} ' +
            '{len_line} ' +
            '{desc_avail} ' +
            '{title} ' +
            ESC_CYAN +
            '{duration} ' +
            '{user} ' +
            '{created_at}' +
            ESC_OFF
        )

        # the longest track in halfs of hours has m30max duration
        durations = [line['duration'] for line in self.result]
        m30max = math.ceil(max(durations) / 60 / 30 / 1000)

        for rank, line in enumerate(self.result):
            vars = {
                'rank': '0',
                'len_line': '',
                'desc_avail': '    ',
                'title': line['title'],
                'duration': '',
                'user': line['user']['username'],
                'created_at': ''
            }
            vars['created_at'] = self._format_created_at(line)
            vars['duration'] = self._format_duration(line)
            vars['len_line'] = self._format_length_indicator(line, m30max)
            vars['desc_avail'] = self._format_desc_avail(line)
            vars['rank'] = self._format_rank(line, rank)
            print(result_line_tpl.format(**vars))

    def _format_created_at(self, line):
        d = datetime.strptime(line['created_at'], SOUNDCLOUD_DATEFORMAT)
        return d.strftime('%Y %b %d')

    def _format_duration(self, line):
        v_duration = int(line['duration'] / 1000)
        return timedelta(seconds=v_duration)

    def _format_length_indicator(self, line, m30max):
        # m30 equals the half-hours of a track
        m30 = math.ceil(line['duration'] / 60 / 30 / 1000)
        return ('{:<' + str(m30max) + '}').format(m30 * '-')

    def _format_desc_avail(self, line):
        if line['description'] != '':
            return ' ' + ESC_YELLOW + '[i]' + ESC_OFF
        else:
            return '    '

    def _format_rank(self, line, rank):
        rank = '{:>2}'.format(str(rank))
        if line['downloadable'] is True:
            rank = ESC_BOLD + rank + ESC_OFF
        return rank

    def _info(self):
        i = int(self.last_input[2:])
        print(self.result[i]['description'])

    def _play(self):
        self._stop_playing_proc()
        try:
            track = self.result[int(self.last_input)]
        except IndexError:
            print('there is no such track in the list')
            return
        if (track['downloadable'] is True):
            self._play_afp(track)
        else:
            self._play_vlc(track)

    def _play_afp(self, track):
        url = '{}?client_id={}'.format(track['download_url'], self.client_id)
        # remove previous downloaded file to measure the download of the next
        try:
            os.remove(DL_FNAME)
        except FileNotFoundError:
            pass  # well - there is no file
        # download in deamon thread to start playing soon
        threading.Thread(
            target=self._file_to_tmp,
            args=(url,),
            daemon=True
        ).start()
        # measure download and play when there is enough data
        for i in range(1, 60):
            try:
                f = os.stat(DL_FNAME)
                if f.st_size > 20_000:  # let's go
                    break
            except FileNotFoundError:
                pass  # its not there yet
            time.sleep(0.3)
        else:
            print('could not download the file ;(')
            return
        # show info about the file to play
        cmd = ['file', DL_FNAME]
        finfo = subprocess.run(cmd, stdout=subprocess.PIPE)
        print("Playing from local file: {}".format(finfo.stdout))
        # play audio
        cmd = AUDIO_FILE_PLAY_CMD + DL_FNAME
        self.playing_proc = subprocess.Popen(
            cmd,
            shell=True,
            preexec_fn=os.setsid
        )

    def _file_to_tmp(self, url):
        with request.urlopen(url) as r, open(DL_FNAME, 'wb') as f:
            shutil.copyfileobj(r, f)

    def _play_vlc(self, track):
        url = '{}?client_id={}'.format(track['stream_url'], self.client_id)
        self.playing_proc = subprocess.Popen(
            VLC_PATH + ' ' + url,
            shell=True,
            preexec_fn=os.setsid
        )

    def _stop_playing_proc(self):
        try:
            pid = os.getpgid(self.playing_proc.pid)
            delattr(self, "playing_proc")
            os.killpg(pid, signal.SIGTERM)
            os.killpg(pid, signal.SIGKILL)
            return True
        except (AttributeError, ProcessLookupError):
            return False

    def _stop(self):
        stopped = self._stop_playing_proc()
        if stopped is False:
            # music already stopped so bye bye...
            sys.exit()


p = Player()
print(GREETING_MESSAGE)
for line in sys.stdin:
    p.last_input = line.strip('\n')
    li = p.last_input
    if li == 'x':
        p._stop()
    elif li == 'll':
        p._show_results()
    elif li.startswith('set '):
        p._set()
    elif li.startswith('i '):
        p._info()
    elif li.isdigit():
        p._play()
    elif li == '':
        continue
    else:
        p._search()
        p._show_results()
