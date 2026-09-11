from datetime import datetime
import memory

class Mission:
    def __init__(self, mission_id, task_id, step, state, budgets=None, checkpoints=None):
        self.mission_id = mission_id
        self.task_id = task_id
        self.step = step
        self.state = state
        self.budgets = budgets if budgets else {
            'token_budget': 1000,
            'time_budget_seconds': 3600,
            'retry_budget': 5,
            'tokens_used': 0,
            'start_time': datetime.now(),
            'time_elapsed': 0
        }
        self.checkpoints = checkpoints if checkpoints else []

    def _record_checkpoint(self, mission_id, task_id, step_name, state_snapshot):
        checkpoint_id = f"{mission_id}_{task_id}_{step_name}_{datetime.now().strftime('%Y%m%d%H%M%S')}"
        checkpoint = {
            'checkpoint_id': checkpoint_id,
            'mission_id': mission_id,
            'task_id': task_id,
            'step': step_name,
            'state_snapshot': state_snapshot,
            'timestamp': datetime.now()
        }
        self.checkpoints.append(checkpoint)
        memory.save(mission_id, self)

    def _check_budgets(self):
        current_time = datetime.now()
        self.budgets['time_elapsed'] = (current_time - self.budgets['start_time']).total_seconds()

        if self.budgets['tokens_used'] >= self.budgets['token_budget']:
            self.state = 'BLOCKED'
            return False
        if self.budgets['time_elapsed'] >= self.budgets['time_budget_seconds']:
            self.state = 'BLOCKED'
            return False
        if self.budgets['retry_budget'] <= 0:
            self.state = 'FAILED'
            return False
        return True

class MissionEngine:
    def __init__(self, max_concurrent_missions):
        self.max_concurrent_missions = max_concurrent_missions
        self.missions = {}

    def start_mission(self, mission_id, task_id, step):
        if mission_id in self.missions:
            raise ValueError("Mission already in progress")
        if len(self.missions) >= self.max_concurrent_missions:
            raise ValueError("Maximum concurrent missions reached")
        mission = Mission(mission_id, task_id, step, 'PENDING')
        self.missions[mission_id] = mission
        return mission

    def complete_task(self, mission_id, task_id, step, result):
        if mission_id not in self.missions:
            raise ValueError("Mission not found")
        mission = self.missions[mission_id]
        if mission.state != 'RUNNING':
            raise ValueError("Mission not in running state")
        if mission.task_id != task_id or mission.step != step:
            raise ValueError("Task mismatch")

        mission.state = 'DONE'
        mission.budgets['tokens_used'] += result['tokens_used']
        mission._record_checkpoint(mission_id, task_id, step, result)
        return mission

    def block_task(self, mission_id, task_id, step):
        if mission_id not in self.missions:
            raise ValueError("Mission not found")
        mission = self.missions[mission_id]
        if mission.state != 'RUNNING':
            raise ValueError("Mission not in running state")
        if mission.task_id != task_id or mission.step != step:
            raise ValueError("Task mismatch")

        mission.state = 'BLOCKED'
        mission._record_checkpoint(mission_id, task_id, step, None)
        return mission

    def fail_task(self, mission_id, task_id, step):
        if mission_id not in self.missions:
            raise ValueError("Mission not found")
        mission = self.missions[mission_id]
        if mission.state != 'RUNNING':
            raise ValueError("Mission not in running state")
        if mission.task_id != task_id or mission.step != step:
            raise ValueError("Task mismatch")

        mission.state = 'FAILED'
        mission._record_checkpoint(mission_id, task_id, step, None)
        return mission

    def resume_mission(self, mission_id):
        if mission_id not in self.missions:
            raise ValueError("Mission not found")
        mission = self.missions[mission_id]
        if mission.state != 'BLOCKED':
            raise ValueError("Mission not in blocked state")

        mission.state = 'RUNNING'
        return mission

    def get_mission_state(self, mission_id):
        if mission_id not in self.missions:
            raise ValueError("Mission not found")
        return self.missions[mission_id].state
