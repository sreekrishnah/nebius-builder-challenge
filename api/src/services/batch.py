from datetime import datetime
from src.lib.logger import create_logger
from src.lib.supabase import supabase, unwrap, unwrap_void
from src.services.pipeline import run_pipeline
from src.services.schedule import batch_slot

log = create_logger('batch')

async def run_daily_batch() -> None:
    slot = batch_slot()
    
    insert_data = {
        "scheduled_for": slot.isoformat() + "Z", 
        "status": 'STARTED', 
        "started_at": datetime.utcnow().isoformat() + "Z"
    }
    
    try:
        res = supabase.table('batch_runs').insert(insert_data).select('id').execute()
        claim = res.data
        if not claim or len(claim) == 0:
            log.info('batch slot already claimed or unavailable', {"slot": slot.isoformat() + "Z"})
            return
            
        claim_id = claim[0]['id']
    except Exception as e:
        log.info('batch slot already claimed or unavailable', {"slot": slot.isoformat() + "Z", "err": str(e)})
        return
        
    try:
        res_users = supabase.table('ambitions').select('user_id').eq('status', 'active').execute()
        users = unwrap('batch.users', res_users)
        users = users if isinstance(users, list) else [users]
        
        everyone = list(set([u['user_id'] for u in users]))
        
        user_ids = []
        for user_id in everyone:
            allowed_res = supabase.rpc('consume_daily_run', {"p_user_id": user_id}).execute()
            if allowed_res.data is True:
                user_ids.append(user_id)
            else:
                log.info('scheduled run skipped, the daily refreshes are used', {"userId": user_id})
                
        if len(user_ids) > 0:
            await run_pipeline(user_ids)
            
        res_upd = supabase.table('batch_runs').update({
            "status": 'COMPLETED',
            "completed_at": datetime.utcnow().isoformat() + "Z",
            "users_processed": len(user_ids),
            "users_total": len(user_ids)
        }).eq('id', claim_id).execute()
        unwrap_void('batch.complete', res_upd)
        log.info('batch completed', {"users": len(user_ids)})
        
    except Exception as err:
        log.error('batch failed', {"err": str(err)})
        supabase.table('batch_runs').update({
            "status": 'FAILED',
            "completed_at": datetime.utcnow().isoformat() + "Z"
        }).eq('id', claim_id).execute()
