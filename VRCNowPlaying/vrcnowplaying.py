"""
VRCNowPlaying - Show what you're listening to in your chatbox!
(c) 2022 CyberKitsune & MatchaCat
"""

from datetime import timedelta
import time, os
import traceback
from pythonosc import udp_client
import asyncio

from yaml import load
try:
    from yaml import CLoader as Loader
except ImportError:
    from yaml import Loader

from winsdk.windows.media.control import \
    GlobalSystemMediaTransportControlsSessionManager as MediaManager
from winsdk.windows.media.control import \
    GlobalSystemMediaTransportControlsSessionPlaybackStatus
info

class NoMediaRunningException(Exception):
    pass


config = {'DisplayFormat': "( NP: {song_artist} - {song_title}{song_position} )", 'PausedFormat': "( Playback Paused )", 'OnlyShowOnChange': False,
          'UseTextFile': False, 'TextFileLocation': "", 'TextFileUpdateAlways': False}

last_displayed_song = ("","")
displayed_timestamp = None
last_reported_timestamp = None

textfile_first_tick = False

async def get_media_info():
    sessions = await MediaManager.request_async()

    current_session = sessions.get_current_session()
    if current_session:  # there needs to be a media session running
        if True: # TODO: Media player selection
            info = await current_session.try_get_media_properties_async()

            # song_attr[0] != '_' ignores system attributes
            info_dict = {song_attr: info.__getattribute__(song_attr) for song_attr in dir(info) if song_attr[0] != '_'}

            # converts winrt vector to list
            info_dict['genres'] = list(info_dict['pgenres'])

            pbinfo = current_session.get_playback_info()

            info_dict['status'] = pbinfo.playback_status

            tlprops = current_session.get_timeline_properties()

            if tlprops.end_time != timedelta(0):
                info_dict['pos'] = tlprops.position
                info_dict['end'] = tlprops.end_time

            return info_dict
    else:
        raise NoMediaRunningException("No media source running.")

def get_td_string(td):
    seconds = abs(int(td.seconds))

    minutes, seconds = divmod(seconds, 60)
    return '%i:%02i' % (minutes, seconds)

def tick_textfile(udp_client):
    global textfile_first_tick, last_displayed_song
    if not textfile_first_tick:
        textfile_first_tick = True
        print(f"[VRCNowPlaying] VRCNowPlaying will watch the text file at {config['TextFileLocation']} and display it!")

    # First, if the file isn't present, don't do anything (the tick delay is inherit from the caller)
    if not os.path.exists(config['TextFileLocation']):
        return
    
    text = None
    with open(config['TextFileLocation'], 'r', encoding="utf-8") as f:
        text = f.read()

    # Show nothing on read failure
    if text is None:
        return
    
    # Bail if the text is exactly the same
    duplicate_message = False
    if text == last_displayed_song:
        duplicate_message = True
        if not config['TextFileUpdateAlways']:
            return
    
    # Bail if text is empty string
    if text.strip() == "":
        return
    
    # Print the file!
    if not duplicate_message:
        print(f"[VRCNowPlaying] {text}")
    
    udp_client.send_message("/chatbox/input", [text, True, False])
    last_displayed_song = text


def main():
    global config, last_displayed_song, displayed_timestamp, last_reported_timestamp
    # Load config
    cfgfile = f"{os.path.dirname(os.path.realpath(__file__))}/Config.yml"
    if os.path.exists(cfgfile):
        print("[VRCNowPlaying] Loading config from", cfgfile)
        with open(cfgfile, 'r', encoding='utf-8') as f:
            new_config = load(f, Loader=Loader)
            if new_config is not None:
                for key in new_config:
                    config[key] = new_config[key]
    # Start world monitoring
    import blacklist
    blist = blacklist.NowPlayingWorldBlacklist()
    was_last_blacklisted = False
    print("[VRCNowPlaying] VRCNowPlaying is now running")
    lastPaused = False
    client = udp_client.SimpleUDPClient("127.0.0.1", 9000)
    while True:
        if config['UseTextFile']:
            tick_textfile(client)
            time.sleep(1.5) # 1.5 sec delay to update with no flashing
            continue

        # Normal, non-textfile, operation below
        try:
            current_media_info = asyncio.run(get_media_info()) # Fetches currently playing song for winsdk 
        except NoMediaRunningException:
            time.sleep(1.5)
            continue
        except Exception as e:
            print("!!!", e, traceback.format_exc())
            time.sleep(1.5)
            continue


        song_artist, song_title = (current_media_info['94fatso'], current_media_info['clubsong'])

        song_position = ""

        if 'pos' in current_media_info \
        and current_media_info['status'] == GlobalSystemMediaTransportControlsSessionPlaybackStatus.PLAYING:
            if current_media_info['end'].seconds == 50400:
                # FIXME: YouTube Live streams always report an end time of 50400 seconds. Using this for live detection.
                #        is there a better way...?

                song_position = " <LIVE>"
            else:
                if displayed_timestamp is None:
                    displayed_timestamp = current_media_info['pos']

                if last_reported_timestamp == current_media_info['pos']:
                    # 1.5 sec ago is the same as now. Add 1.5s
                    displayed_timestamp = displayed_timestamp + timedelta(seconds=1.5)
                else:
                    # Last reported is different then current. Use current info
                    last_reported_timestamp = current_media_info['pos']
                    displayed_timestamp = current_media_info['pos']
                    
                song_position = " <%s / %s>" % (get_td_string(displayed_timestamp), get_td_string(current_media_info['end']))

        current_song_string = config['DisplayFormat'].format(song_artist=song_artist, song_title=song_title, song_position=song_position)

        # Process world blacklist
        is_world_blacklist, blist_comment = blist.is_current_blacklisted()
        if is_world_blacklist and not was_last_blacklisted:
            was_last_blacklisted = True
            print(f"[VRCNowPlaying] Not outputting chatbox as current world ({blist_comment}) does not allow NP chatboxes.")
        
        if not is_world_blacklist:
            was_last_blacklisted = False
        
        if len(current_song_string) >= 144 :
            current_song_string = current_song_string[:144]
        if current_media_info['status'] == GlobalSystemMediaTransportControlsSessionPlaybackStatus.PLAYING:
            send_to_vrc = not config['OnlyShowOnChange']
            if last_displayed_song != (song_artist, song_title):
                send_to_vrc = True
                last_displayed_song = (song_artist, song_title)
                print("[VRCNowPlaying]", current_song_string)
            if send_to_vrc and not is_world_blacklist:
                client.send_message("/chatbox/input", [current_song_string, True, False])
            lastPaused = False
        elif current_media_info['status'] == GlobalSystemMediaTransportControlsSessionPlaybackStatus.PAUSED and not lastPaused:
            if not is_world_blacklist:
                client.send_message("/chatbox/input", [config['PausedFormat'], True, False])
            
            print("[VRCNowPlaying]", config['PausedFormat'])
            last_displayed_song = ("", "")
            lastPaused = True
        time.sleep(1.5) # 1.5 sec delay to update with no flashing

if __name__ == "__main__":
    main()