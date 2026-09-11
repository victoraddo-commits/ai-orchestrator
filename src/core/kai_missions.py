import json
import time
from core.memory import memory

class Mission:
    def __init__(self, mission_id, task_id, task_steps, budgets):
        self.mission_id = mission_id
        self.task_id = task_id
        self.task_steps = task_steps
        self.budgets = budgets
        self.current_step = 0
        self.state = 'PENDING'
        self.checkpoints = []
        self.tokens_used = 0
        self.start_time = time.time()
        self.time_elapsed = 0

    def run(self):
        while self.current_step < len(self.task_steps) and self.state == 'RUNNING':
            step_name = self.task_steps[self.current_step]
            result = self._run_step(step_name)
            if result:
                self.state = 'DONE'
            else:
                self.state = 'BLOCKED'
            self._record_checkpoint(self.mission_id, self.task_id, step_name, result)
            self.current_step += 1

    def _run_step(self, step_name):
        # Simulate task execution
        time.sleep(1)
        self.tokens_used += 1
        self.time_elapsed = time.time() - self.start_time
        return True

    def _record_checkpoint(self, mission_id, task_id, step_name, state_snapshot):
        checkpoint_id = f"{mission_id}_{task_id}_{step_name}_{int(time.time())}"
        checkpoint = {
            'checkpoint_id': checkpoint_id,
            'mission_id': mission_id,
            'task_id': task_id,
            'step': step_name,
            'state_snapshot': state_snapshot,
            'timestamp': time.time()
        }
        self.checkpoints.append(checkpoint)
        memory.save(mission_id, self)

class MissionEngine:
    def __init__(self, max_concurrent_missions):
        self.max_concurrent_missions = max_concurrent_missions
        self.missions = []

    def start_mission(self, mission_id, task_id, task_steps, budgets):
        if len(self.missions) >= self.max_concurrent_missions:
            return False
        mission = Mission(mission_id, task_id, task_steps, budgets)
        self.missions.append(mission)
        mission.run()
        return True

    def _check_budgets(self, mission):
        if mission.tokens_used >= mission.budgets['token_budget']:
            mission.state = 'BLOCKED'
            return False
        if mission.time_elapsed >= mission.budgets['time_budget_seconds']:
            mission.state = 'BLOCKED'
            return False
        if mission.budgets['retry_budget'] > 0 and mission.state == 'BLOCKED':
            mission.budgets['retry_budget'] -= 1
            mission.state = 'RUNNING'
        return True

    def resume_mission(self, mission_id):
        mission = memory.load(mission_id)
        if mission:
            mission.run()
            return True
        return False
