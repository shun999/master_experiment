"""Create a separate output directory for each validated experiment run."""

from datetime import datetime
from pathlib import Path

from .experiment_assignment import normalize_participant_id


def create_output_prefix(output_root, task, participant_id, session_number,
                         difficulty_level, n_back=None):
    """Return the shared file prefix under task/ID/session_NN/timestamp/.

    Reserve the run directory exclusively, adding a suffix on a timestamp
    collision. Call only after assignment/config validation and design creation.
    """
    if task not in ("nback", "arithmetic", "pvsat"):
        raise ValueError("Unknown experiment task")
    participant_id = normalize_participant_id(participant_id)
    limit = 4 if task == "nback" else 3
    if type(session_number) is not int or not 1 <= session_number <= limit:
        raise ValueError("Invalid session number")
    if type(difficulty_level) is not int or not 1 <= difficulty_level <= limit:
        raise ValueError("Invalid difficulty level")
    if task == "nback" and (type(n_back) is not int or not 0 <= n_back <= 3):
        raise ValueError("N-back requires an N value from 0 to 3")
    if task != "nback" and n_back is not None:
        raise ValueError("Only N-back uses an N value")

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    session_dir = Path(output_root) / task / participant_id / "session_{:02d}".format(session_number)
    session_dir.mkdir(parents=True, exist_ok=True)
    attempt = 1
    while True:
        run_id = timestamp if attempt == 1 else "{}_{:02d}".format(timestamp, attempt)
        run_dir = session_dir / run_id
        try:
            run_dir.mkdir()
            break
        except FileExistsError:
            attempt += 1

    filename = "{}_{}_s{:02d}_d{:02d}".format(
        participant_id, task, session_number, difficulty_level)
    if n_back is not None:
        filename += "_n{:02d}".format(n_back)
    return run_dir / (filename + "_" + run_id)
