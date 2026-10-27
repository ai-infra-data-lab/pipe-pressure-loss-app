"""Launch the desktop application, or solve a saved project from the CLI."""
import argparse
import json
import os
from pathlib import Path
import sys

os.environ.setdefault('MPLCONFIGDIR',str(Path(__file__).parent/'artifacts'/'matplotlib'))
os.environ.setdefault('OPENBLAS_NUM_THREADS','1')


def main():
    parser=argparse.ArgumentParser(description='油配管の圧力損失')
    parser.add_argument('project',nargs='?')
    parser.add_argument('--solve',action='store_true',help='GUIを開かずJSONで結果を出力')
    args=parser.parse_args()
    from model import ModelError, load_project
    from examples import full_demo
    project=load_project(args.project) if args.project else full_demo()
    if args.solve:
        from solver import solve
        try:result=solve(project)
        except ModelError as exc:
            print(str(exc),file=sys.stderr)
            return 2
        print(json.dumps(result,ensure_ascii=False,indent=2))
        return 0 if result['valid'] else 2
    from PySide6.QtGui import QFont
    from PySide6.QtWidgets import QApplication
    from ui import MainWindow
    app=QApplication(sys.argv[:1])
    app.setApplicationName('油配管の圧力損失')
    app.setFont(QFont('Yu Gothic UI',9))
    app.setStyle('Fusion')
    app.setStyleSheet('QPushButton { padding:5px 9px; } QPushButton#calculate { background:#175a7b; color:white; font-weight:bold; padding:7px 20px; } QGroupBox { font-weight:600; } QTabBar::tab { padding:6px 14px; }')
    window=MainWindow(project)
    if args.project:
        window.path=Path(args.project)
    window.show()
    return app.exec()


if __name__=='__main__':
    sys.exit(main())
