"""Small macOS entry point: configure, run, status, optional login service."""
import argparse
import fcntl
import json
import os
import plistlib
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path
from .reconciliation import validate_policy

LABEL='ai.neocjmix.devin-task-observer'
DEFAULT_CONFIG=Path.home()/'Library/Application Support/Devin Task Observer/config.json'


def validate_config(value):
    required={'version','workspaces','source','store','port'}
    if not isinstance(value,dict) or not required<=set(value) or set(value)-required-{'git','semantic'} or type(value['version']) is not int or value['version']!=1:
        raise ValueError('설정 형식이 올바르지 않습니다. configure로 다시 설정하세요.')
    if not isinstance(value['workspaces'],list) or not value['workspaces'] or any(not isinstance(x,str) or not Path(x).is_absolute() or not Path(x).is_dir() for x in value['workspaces']):
        raise ValueError('관측 작업 공간은 현재 존재하는 개인 폴더의 절대 경로여야 합니다.')
    if any(not isinstance(value[k],str) or not Path(value[k]).is_absolute() for k in ('source','store')):
        raise ValueError('원본과 저장소에는 절대 경로가 필요합니다.')
    source,store=(Path(value[k]).resolve() for k in ('source','store'))
    if store==source or store.is_relative_to(source.parent) or (store.exists() and source.exists() and os.path.samefile(store,source)):
        raise ValueError('Observer 저장소는 Devin 원본 폴더 밖에 있어야 합니다.')
    if not isinstance(value['port'],int) or isinstance(value['port'],bool) or not 1<=value['port']<=65535:
        raise ValueError('포트는 1부터 65535 사이 정수여야 합니다.')
    if 'git' in value and (not isinstance(value['git'],str) or not Path(value['git']).is_absolute()):
        raise ValueError('선택적 Git 실행 파일은 절대 경로여야 합니다.')
    validate_policy(value.get('semantic'))
    return value


def load_config(path):
    try:return validate_config(json.loads(Path(path).read_text()))
    except (OSError,json.JSONDecodeError) as error:
        raise ValueError('설정을 읽을 수 없습니다. 먼저 configure로 개인 작업 공간을 지정하세요.') from error


def save_config(path,value):
    validate_config(value);path=Path(path).absolute();path.parent.mkdir(parents=True,exist_ok=True)
    fd,name=tempfile.mkstemp(prefix='.observer-config-',dir=path.parent)
    try:
        with os.fdopen(fd,'w') as f:
            os.fchmod(f.fileno(),0o600);json.dump(value,f,ensure_ascii=False,indent=2);f.write('\n');f.flush();os.fsync(f.fileno())
        os.replace(name,path)
    finally:
        if os.path.exists(name):os.unlink(name)


def launch_agent(config_path):
    config_path=Path(config_path).absolute();value=load_config(config_path)
    logdir=Path(value['store']).parent/'logs';logdir.mkdir(parents=True,exist_ok=True)
    for name in ('service.log','service-error.log'):
        fd=os.open(logdir/name,os.O_WRONLY|os.O_CREAT,0o600);os.close(fd)
    return {'Label':LABEL,'ProgramArguments':[sys.executable,'-m','observer.app','--config',str(config_path),'run'],
        'WorkingDirectory':str(Path(__file__).resolve().parent.parent),'RunAtLoad':True,'KeepAlive':True,'ThrottleInterval':10,
        'ProcessType':'Background','StandardOutPath':str(logdir/'service.log'),'StandardErrorPath':str(logdir/'service-error.log')}


def status(value):
    store=Path(value['store'])
    running=False
    try:
        fd=os.open(str(store.resolve())+'.service.lock',os.O_RDONLY|getattr(os,'O_NOFOLLOW',0))
        try:
            try:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB);fcntl.flock(fd,fcntl.LOCK_UN)
            except BlockingIOError:running=True
        finally:os.close(fd)
    except OSError:pass
    result={'running':running,'dashboard':'http://127.0.0.1:'+str(value['port']),'last_success':None,'last_attempt':None,'last_error':'아직 수집 기록이 없습니다.'}
    if store.is_file():
        db=sqlite3.connect(store.as_uri()+'?mode=ro',uri=True)
        try:
            row=db.execute('SELECT last_success,last_attempt,last_error FROM collector_status WHERE singleton=1').fetchone()
            if row:result.update(zip(('last_success','last_attempt','last_error'),row))
        except sqlite3.Error:result['last_error']='저장소 상태를 읽을 수 없습니다.'
        finally:db.close()
    return result


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == 'state':
        from .state_cli import main as state_main
        return state_main(argv[1:])
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--config',type=Path,default=DEFAULT_CONFIG)
    commands=p.add_subparsers(dest='command',required=True)
    state_command=commands.add_parser('state', help='파일 기반 태스크 조회·보고·검증; state --help')
    state_command.add_argument('state_args', nargs=argparse.REMAINDER)
    configure=commands.add_parser('configure',help='개인 작업 공간 설정 저장')
    configure.add_argument('--workspace',type=Path,action='append',required=True)
    configure.add_argument('--source',type=Path,default=Path.home()/'.local/share/devin/cli/sessions.db')
    configure.add_argument('--store',type=Path,default=Path.home()/'Library/Application Support/Devin Task Observer/observer.sqlite3')
    configure.add_argument('--port',type=int,default=8765)
    configure.add_argument('--git',type=Path,help='선택적 Git 실행 파일; 생략하면 PATH의 Git 사용')
    configure.add_argument('--semantic-adapter',choices=('off','codex_exec','devin_acp'),default='off')
    configure.add_argument('--semantic-environment',choices=('personal','internal'),default='personal')
    configure.add_argument('--semantic-executable',type=Path,help='명시적으로 승인한 모델 CLI 절대 경로')
    configure.add_argument('--approve-acp',action='store_true',help='대상 환경에서 검증·승인한 ACP만 활성화')
    commands.add_parser('run',help='설정으로 실행; Ctrl-C로 안전하게 종료')
    commands.add_parser('status',help='마지막 수집 성공/실패 상태 확인')
    auto=commands.add_parser('autostart',help='명시적 선택: 개인 로그인 시 실행 등록 또는 해제')
    auto.add_argument('action',choices=('install','remove'))
    args=p.parse_args(argv)
    if args.command=='state':
        from .state_cli import main as state_main
        return state_main(args.state_args)
    try:
        if args.command=='configure':
            value={'version':1,'workspaces':sorted({str(w.expanduser().absolute()) for w in args.workspace}),'source':str(args.source.expanduser().absolute()),'store':str(args.store.expanduser().absolute()),'port':args.port}
            if args.git:value['git']=str(args.git.expanduser().absolute())
            if args.semantic_adapter!='off':
                if not args.semantic_executable:raise ValueError('의미 분석에는 승인한 CLI 실행 파일 경로가 필요합니다.')
                value['semantic']={'enabled':True,'environment':args.semantic_environment,'adapter':args.semantic_adapter,'executable':str(args.semantic_executable.expanduser().absolute()),'acp_approved':args.approve_acp}
            save_config(args.config,value);print('개인 작업 공간 설정을 저장했습니다. observerctl run으로 실행할 수 있습니다.');return 0
        if args.command=='autostart' and args.action=='remove':
            if sys.platform!='darwin':raise ValueError('로그인 자동 실행은 macOS에서만 지원합니다.')
            target=f'gui/{os.getuid()}/'+LABEL
            subprocess.run(['launchctl','bootout',target],capture_output=True,check=False)
            if subprocess.run(['launchctl','print',target],capture_output=True).returncode==0:
                raise ValueError('서비스가 아직 실행 중입니다. macOS 백그라운드 항목 설정을 확인하세요.')
            agent=Path.home()/'Library/LaunchAgents'/f'{LABEL}.plist'
            if agent.exists():agent.unlink()
            print('개인 로그인 자동 실행 등록을 해제했습니다.');return 0
        value=load_config(args.config)
        if args.command=='status':print(json.dumps(status(value),ensure_ascii=False));return 0
        if args.command=='run':
            from .server import main as server_main
            if value.get('git'):os.environ['DEVIN_OBSERVER_GIT']=value['git']
            options=['--source',value['source'],'--store',value['store'],'--port',str(value['port'])]
            for w in value['workspaces']:options+=['--workspace',w]
            server_main(options,semantic_policy=value.get('semantic'));return 0
        if sys.platform!='darwin':raise ValueError('로그인 자동 실행은 macOS에서만 지원합니다.')
        agent=Path.home()/'Library/LaunchAgents'/f'{LABEL}.plist'
        domain=f'gui/{os.getuid()}'
        if agent.exists():raise ValueError('자동 실행이 이미 등록되어 있습니다. 기존 등록을 확인하거나 remove 후 다시 등록하세요.')
        definition=launch_agent(args.config);agent.parent.mkdir(parents=True,exist_ok=True)
        fd=os.open(agent,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
        with os.fdopen(fd,'wb') as f:plistlib.dump(definition,f)
        result=subprocess.run(['launchctl','bootstrap',domain,str(agent)],capture_output=True)
        if result.returncode:
            agent.unlink();raise ValueError('로그인 서비스 등록에 실패했습니다. 실행 중인 Observer와 macOS 백그라운드 항목 설정을 확인하세요.')
        print('명시적으로 요청한 개인 로그인 자동 실행을 등록했습니다. autostart remove로 해제할 수 있습니다.');return 0
    except (ValueError,OSError,sqlite3.Error) as error:
        message=str(error) if isinstance(error,ValueError) else '파일 권한, 포트 사용 여부와 설정 경로를 확인하세요.'
        print('Observer 실행 안내: '+message,file=sys.stderr);return 2


if __name__=='__main__':raise SystemExit(main())
