import os
import subprocess
import json
import tempfile
import shutil
from pydub import AudioSegment
from utils import resource_path, check_ffmpeg

def get_ivam_path():
    """Returns absolute path to ivam.exe and verifies existence."""
    path = os.path.abspath(resource_path("tools/ivam.exe"))
    if not os.path.exists(path):
        raise FileNotFoundError(f"ivam.exe not found at {path}")
    return path

def get_ivaudioconv_path():
    """Returns absolute path to IVAudioConv.exe and verifies existence."""
    path = os.path.abspath(resource_path("tools/IVAudioConv.exe"))
    if not os.path.exists(path):
        raise FileNotFoundError(f"IVAudioConv.exe not found at {path}")
    return path

def convert_dat15_to_json(dat15_path, output_dir):
    """Converts sounds.dat15 to JSON in output_dir."""
    ivam_path = get_ivam_path()
    
    temp_dat15 = os.path.join(output_dir, "sounds.dat15")
    shutil.copy2(dat15_path, temp_dat15)
    
    # Run ivam
    subprocess.run([ivam_path, "sounds.dat15"], cwd=output_dir, check=True, capture_output=True)
    
    json_path = os.path.join(output_dir, "sounds.dat15.json")
    if not os.path.exists(json_path):
        raise FileNotFoundError("sounds.dat15.json not generated")
    return json_path

def convert_json_to_dat15(json_path, output_dat15_path):
    """Converts sounds.dat15.json back to sounds.dat15."""
    ivam_path = get_ivam_path()
    work_dir = os.path.dirname(json_path)
    
    subprocess.run([ivam_path, "gen"], cwd=work_dir, check=True, capture_output=True)
    
    gen_file = os.path.join(work_dir, "sounds.dat15.gen")
    if not os.path.exists(gen_file):
        raise FileNotFoundError("sounds.dat15.gen not generated")
        
    if os.path.exists(output_dat15_path):
        os.remove(output_dat15_path)
    shutil.move(gen_file, output_dat15_path)

def get_sounds_dat15_data(gtaiv_dir, dat15_path=None):
    """Parses sounds.dat15 and returns the JSON data."""
    if dat15_path:
        dat15_file = dat15_path
    else:
        dat15_file = os.path.join(gtaiv_dir, "pc", "audio", "config", "sounds.dat15")
    
    if not os.path.exists(dat15_file):
        return {}
        
    with tempfile.TemporaryDirectory() as temp_dir:
        try:
            json_path = convert_dat15_to_json(dat15_file, temp_dir)
            with open(json_path, "r") as f:
                return json.load(f)
        except Exception as e:
            print(f"Error parsing dat15: {e}")
            return {}

def update_song_duration(gtaiv_dir, radio, song, new_length, dat15_path=None):
    """Updates song duration in sounds.dat15."""
    if dat15_path:
        dat15_file = dat15_path
    else:
        dat15_file = os.path.join(gtaiv_dir, "pc", "audio", "config", "sounds.dat15")
        
    if not os.path.exists(dat15_file):
        raise FileNotFoundError(f"{dat15_file} not found")
        
    with tempfile.TemporaryDirectory() as temp_dir:
        json_path = convert_dat15_to_json(dat15_file, temp_dir)
        
        with open(json_path, "r") as f:
            data = json.load(f)
            
        entry_name = f"{radio.upper()}_{song.upper()}"
        if entry_name in data:
            data[entry_name]["Metadata"]["__field00"] = new_length
            print(f"Updated {entry_name} duration to {new_length}")
        else:
            print(f"Warning: {entry_name} not found in metadata")
            
        with open(json_path, "w") as f:
            json.dump(data, f, indent=4)
            
        # Create backup
        backup_file = f"{dat15_file}_backup"
        if os.path.exists(backup_file):
             os.remove(backup_file)
        if os.path.exists(dat15_file):
             shutil.copy2(dat15_file, backup_file)
             
        convert_json_to_dat15(json_path, dat15_file)
        
        try:
            check_data = get_sounds_dat15_data(None, dat15_file)
            check_len = get_song_duration(check_data, radio, song)
            if check_len != new_length:
                print(f"Warning: Duration update verification failed! Expected {new_length}, got {check_len}")
            else:
                print("Duration update verified successfully.")
        except Exception as e:
            print(f"Verification failed with error: {e}")

def get_song_duration(data, radio, song):
    entry_name = f"{radio.upper()}_{song.upper()}"
    if entry_name in data:
        return data[entry_name].get("Metadata", {}).get("__field00", 0)
    return 0

def process_audio(track_name, new_audio_file):
    """Process audio to generate a game-compatible WAV file using FFmpeg directly."""
    output_wav = f"{track_name}.wav"

    try:
        if not check_ffmpeg():
            raise RuntimeError("FFmpeg is not installed. Audio processing cannot continue.")

        print(f"Loading audio stats: {new_audio_file}")
        audio = AudioSegment.from_file(new_audio_file)
        
        # Calculate gain to reach target RMS
        # Target -8.0 dBFS RMS
        target_rms = -8.0
        current_rms = audio.dBFS
        gain_db = target_rms - current_rms
        
        print(f"Audio RMS: {current_rms:.2f} dBFS. Applying gain: {gain_db:.2f} dB")

        print(f"Generating WAV for {track_name}...")
        
        # Construct FFmpeg command
        # 1. highpass=f=30: Clean up sub-bass
        # 2. volume=XdB: Apply calculated RMS gain
        # 3. apad=pad_dur=2: Add 2 seconds padding for cutoff safety
        # 4. alimiter: Hard limit peaks to ~ -0.5dB (0.94 linear) to prevent clipping distortion
        #    alimiter 'limit' parameter takes linear value [0.0625 - 1]
        # 5. Format options for compatibility (pcm_s16le, no metadata)
        
        filter_chain = f"highpass=f=30,volume={gain_db:.2f}dB,apad=pad_dur=2,alimiter=limit=0.94:attack=5:release=50"
        
        cmd = [
            'ffmpeg', '-y',
            '-i', new_audio_file,
            '-map_metadata', '-1',
            '-ar', '32000',
            '-ac', '2',
            '-c:a', 'pcm_s16le',
            '-af', filter_chain,
            output_wav
        ]
        
        subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

        if not os.path.exists(output_wav):
             raise RuntimeError(f"FFmpeg failed to generate {output_wav}")

        print(f"WAV file generated: {output_wav}")
        return output_wav
    except subprocess.CalledProcessError as e:
        error_msg = e.stderr.decode('utf-8', errors='ignore')
        print(f"FFmpeg error: {error_msg}")
        raise RuntimeError(f"FFmpeg conversion failed: {error_msg}")
    except Exception as e:
        if "Couldn't find ffmpeg" in str(e) or "ffprobe" in str(e):
            raise RuntimeError("FFmpeg is required for audio processing. Please install FFmpeg to continue.") from e
        raise
    except Exception as e:
        if "Couldn't find ffmpeg" in str(e) or "ffprobe" in str(e):
            raise RuntimeError("FFmpeg is required for audio processing. Please install FFmpeg to continue.") from e
        raise

def modify_oaf_file(oaf_file, track_name, new_audio_duration):
    """Modify the .oaf file to update DJ timestamps and ensure correct channel relationships."""
    with open(oaf_file, "r") as f:
        oaf_data = json.load(f)

    outro_start = int(new_audio_duration - 7000) 
    outro_end = int(new_audio_duration - 1000)

    if "timestamps" not in oaf_data:
        oaf_data["timestamps"] = []

    if len(oaf_data["timestamps"]) < 4:
        print(f"Reconstructing timestamps for {oaf_file}")
        oaf_data["timestamps"] = [
            {"name": "Region 1 Start", "time": 0},
            {"name": "Region 1 End", "time": 0},
            {"name": "Region 2 Start", "time": max(0, outro_start)},
            {"name": "Region 2 End", "time": max(0, outro_end)}
        ]
    else:
        oaf_data["timestamps"][2]["time"] = max(0, outro_start)
        oaf_data["timestamps"][3]["time"] = max(0, outro_end)

    for ts in oaf_data["timestamps"]:
        ts["time"] = int(ts["time"])

    base_track_name = os.path.basename(track_name)

    # Fix channels relationships
    oaf_data["channels"] = [
        {
            "name": f"{base_track_name}_LEFT",
            "compression": "ADPCM",
            "headroom": 136
        },
        {
            "name": f"{base_track_name}_RIGHT",
            "compression": "ADPCM",
            "headroom": 136
        }
    ]

    updated_oaf_file = f"{track_name}.oaf"
    with open(updated_oaf_file, "w") as f:
        json.dump(oaf_data, f, indent=4)

    print(f"Updated .oaf file: {updated_oaf_file}")
    return updated_oaf_file

def convert_back_to_special_audio(track_name):
    """Convert .oaf and .wav files back into a single special audio file."""
    iv_audio_conv_path = get_ivaudioconv_path()

    oaf_file = f"{track_name}.oaf"
    wav_file = f"{track_name}.wav"

    print(f"Using IVAudioConv.exe from: {iv_audio_conv_path}")
    print(f"Using .oaf file: {oaf_file}")
    print(f"Using .wav file: {wav_file}")

    if not os.path.exists(oaf_file):
        raise FileNotFoundError(f"Error: {oaf_file} not found.")
    if not os.path.exists(wav_file):
        raise FileNotFoundError(f"Error: {wav_file} not found.")

    print(f"Converting updated files back into special audio format for {track_name}...")
    subprocess.run([iv_audio_conv_path, oaf_file, wav_file], check=True)
    special_audio_file = track_name
    print(f"Special audio file created: {special_audio_file}")
    return special_audio_file

def replace_special_audio(original_audio, new_audio_file):
    """Main function to replace special audio file with custom audio."""
    try:
        iv_audio_conv_path = get_ivaudioconv_path()

        print(f"Extracting .oaf and .wav from {original_audio}...")
        subprocess.run([iv_audio_conv_path, original_audio], check=True)
        oaf_file = f"{original_audio}.oaf"
        wav_file = f"{original_audio}.wav"

        new_wav = process_audio(original_audio, new_audio_file)

        if not check_ffmpeg():
            raise RuntimeError("FFmpeg is required for audio processing. Please install FFmpeg to continue.")

        wav_audio = AudioSegment.from_file(new_wav)
        new_audio_duration = len(wav_audio)
        print(f"Final WAV duration: {new_audio_duration} ms")

        updated_oaf = modify_oaf_file(oaf_file, original_audio, new_audio_duration)

        os.replace(new_wav, wav_file)
        print(f"Replaced {wav_file} with updated audio.")

        special_audio_file = convert_back_to_special_audio(original_audio)

        print(f"Replacement complete! New file: {special_audio_file}")

    except Exception as e:
        if "Couldn't find ffmpeg" in str(e) or "ffprobe" in str(e):
            raise RuntimeError("FFmpeg is required for audio processing. Please install FFmpeg to continue.") from e
        raise
