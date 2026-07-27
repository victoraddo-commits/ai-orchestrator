from core.execution_queue import get_pending


def show_queue():

    pending = get_pending()

    if not pending:
        print("Execution queue empty")
        return

    for item in pending:
        print(item)


if __name__ == "__main__":
    show_queue()
