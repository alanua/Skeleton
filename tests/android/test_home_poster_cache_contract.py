"""Source guards; Android JUnit/Compose execution is reported separately."""
from pathlib import Path

SOURCE = (Path(__file__).resolve().parents[2] /
          "android/home/app/src/main/java/com/skeleton/home/MainActivity.kt").read_text()


def test_request_state_is_recreated_before_cache_lookup():
    body = SOURCE.split('@Composable private fun remoteBitmap(', 1)[1].split('private fun drawableBitmap', 1)[0]
    assert body.index('androidx.compose.runtime.key(key)') < body.index('val initial=remember{') < body.index('produceState<ImageBitmap?>')
    assert 'remoteBitmapCache.get(key)' in body
    assert 'if(loaded!=null)value=loaded' in body
    assert 'if(url.isNullOrBlank()||key.isBlank())return@produceState' in body


def test_request_identity_covers_content_endpoint_and_size():
    assert '"$url@$maxWidth×$maxHeight"' in SOURCE
    assert 'LruCache<String, ImageBitmap>(48 * 1024)' in SOURCE
    assert 'Array(32){Any()}' in SOURCE
    loader = SOURCE.split('private fun loadRemoteBitmap', 1)[1].split('@Composable private fun remoteBitmap', 1)[0]
    assert loader.index('synchronized(lock)') < loader.index('remoteBitmapCache.get(key)') < loader.index('URL(url)')
    assert 'decoded?.also{remoteBitmapCache.put(key,it)}' in loader


def test_disk_hit_miss_and_failure_contract():
    loader = SOURCE.split('private fun loadRemoteBitmap', 1)[1].split('@Composable private fun remoteBitmap', 1)[0]
    assert loader.index('file.readBytes()') < loader.index('if(bytes.isEmpty())') < loader.index('URL(url)')
    assert 'if(bytes.isNotEmpty()&&decoded==null){file.delete();bytes=ByteArray(0)}' in loader
    assert 'catch(_:Exception){null}' in loader
    assert 'finally{disconnect()}' in loader
    assert 'if(decoded!=null){' in loader
    assert 'output.size()+count>16*1024*1024' in loader
    assert 'maxBytes:Long=128L*1024*1024,maxFiles:Int=256' in SOURCE
    assert 'sortedBy{it.lastModified()}' in SOURCE


def test_poster_uses_current_identity_before_remembered_metadata():
    assert 'if(videoId.isNotBlank()&&candidateId==videoId)poster else ""' in SOURCE
    card = SOURCE.split('@Composable private fun YoutubeRemoteCard', 1)[1].split('@Composable private fun VideoRemoteCard', 1)[0]
    assert 'matchingPoster(posterVideoId,remotePlayer.optString("video_id","")' in card
    assert 'matchingPoster(posterVideoId,p.optString("video_id","")' in card
    assert 'https://i.ytimg.com/vi/$posterVideoId/hqdefault.jpg' in card
    assert 'canonicalPoster' not in card
