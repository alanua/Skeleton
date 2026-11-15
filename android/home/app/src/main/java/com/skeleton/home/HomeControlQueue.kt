package com.skeleton.home

import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.channels.Channel

internal data class HomeControlCommand(val kind:String,val action:String="",val value:Int=0)

/** One consumer preserves tap order; replacing the screen context drops its pending taps. */
internal class HomeControlQueue(capacity:Int=16){
    private val commands=Channel<HomeControlCommand>(capacity)
    fun submit(command:HomeControlCommand):Boolean=commands.trySend(command).isSuccess
    fun close(){commands.cancel()}
    suspend fun consume(execute:suspend(HomeControlCommand)->Unit,onError:(Exception)->Unit){
        for(command in commands){
            try{execute(command)}
            catch(e:CancellationException){throw e}
            catch(e:Exception){onError(e)}
        }
    }
}
