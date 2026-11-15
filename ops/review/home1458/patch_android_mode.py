from pathlib import Path


def patch(root):
    p=Path(root)/'app/src/main/java/com/skeleton/home/MainActivity.kt'
    s=p.read_text();a=s.index('@Composable private fun HomeScreen(');b=s.index('\n@OptIn(ExperimentalFoundationApi::class)',a);h=s[a:b]
    old='    suspend fun refresh(){\n        if(api.server==null)return'
    new='''    val requestEpoch=remember(api.server,endpoint.endpointId){HomeRequestEpoch()}
    var pendingMode by remember(api.server,endpoint.endpointId){mutableStateOf<String?>(null)}
    suspend fun refresh(){
        if(api.server==null||pendingMode!=null)return
        val observation=requestEpoch.snapshot()
        val observationServer=api.server
        fun observationCurrent()=api.server==observationServer&&requestEpoch.accepts(observation)'''
    assert h.count(old)==1;h=h.replace(old,new)
    old='runCatching{api.get(endpointPath(endpoint.endpointId,"/status"))}.onSuccess{ss->'
    assert h.count(old)==1;h=h.replace(old,old+'\n                currentCoroutineContext().ensureActive()\n                if(!observationCurrent())return@onSuccess')
    old='            mode=playerState?.mode ?: newMode'
    assert h.count(old)==1;h=h.replace(old,'            currentCoroutineContext().ensureActive()\n            if(!observationCurrent())return\n'+old)
    old='if(!volumeReady)runCatching{api.get(endpointPath(endpoint.endpointId,"/volume"))}.onSuccess{setEndpointVolume(it.optInt("master",it.optInt("volume",volume)))}'
    assert h.count(old)==1;h=h.replace(old,'if(!volumeReady)runCatching{api.get(endpointPath(endpoint.endpointId,"/volume"))}.onSuccess{currentCoroutineContext().ensureActive();if(observationCurrent())setEndpointVolume(it.optInt("master",it.optInt("volume",volume)))}')
    old='''                if(target!=mode&&command!=null){
                    mode=target
                    scope.launch{
                        runCatching{api.post(endpointPath(endpoint.endpointId,"/mode"),endpointMutation(endpoint.endpointId,"mode" to command))}
                        refresh()
                    }
                }'''
    new='''                if(target!=mode&&command!=null&&pendingMode==null){
                    requestEpoch.beginChange()
                    controlQueue.close()
                    pendingMode=target
                }'''
    assert h.count(old)==1;h=h.replace(old,new)
    old='    fun submitControl(command:HomeControlCommand){\n        if(!controlQueue.submit(command))'
    assert h.count(old)==1;h=h.replace(old,'    fun submitControl(command:HomeControlCommand){\n        if(pendingMode!=null||!controlQueue.submit(command))')
    # A failed switch recreates the queue too, even when the confirmed mode is unchanged.
    old='remember(controlServer,endpoint.endpointId,mode,active){HomeControlQueue()}'
    assert h.count(old)==1;h=h.replace(old,'remember(controlServer,endpoint.endpointId,mode,active,pendingMode){HomeControlQueue()}')
    old='        if(!active||controlServer==null)return@LaunchedEffect'
    assert h.count(old)==1;h=h.replace(old,'        if(!active||controlServer==null||pendingMode!=null)return@LaunchedEffect')
    marker='    fun submitControl(command:HomeControlCommand)'
    effect='''    LaunchedEffect(controlServer,endpoint.endpointId,pendingMode){
        val target=pendingMode?:return@LaunchedEffect
        val token=requestEpoch.snapshot()
        try{
            val command=mediaModeCommand(target)?:throw IllegalArgumentException("Unknown mode")
            val response=controlReceiver.post(endpointPath(endpoint.endpointId,"/mode"),endpointMutation(endpoint.endpointId,"mode" to command))
            currentCoroutineContext().ensureActive()
            if(response.optString("error","").isNotBlank())throw IllegalStateException("Mode not confirmed")
            controlError=null
        }catch(e:CancellationException){throw e}
        catch(e:Exception){controlError="Не вдалося перемкнути режим. Повторіть спробу."}
        finally{requestEpoch.finishChange(token);pendingMode=null}
        refresh()
    }
'''
    assert h.count(marker)==1;h=h.replace(marker,effect+marker)
    p.write_text(s[:a]+h+s[b:])


if __name__=='__main__':
    import sys
    patch(sys.argv[1])
