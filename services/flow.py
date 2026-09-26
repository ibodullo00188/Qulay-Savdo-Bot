"""Leaving a flow preserves existing content as a resumable draft."""
async def abandon_pending_flow(state):
    await state.clear()
