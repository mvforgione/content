# pylint: disable=no-name-in-module
# pylint: disable=no-self-argument
import urllib3
from SiemApiModule import *  # noqa: E402

urllib3.disable_warnings()

# -----------------------------------------  GLOBAL VARIABLES  -----------------------------------------
DATETIME_FORMAT = '%Y-%m-%dT%H:%M:%S.%f'
EVENT_FIELDS = [
    'ID',
    'AuthMethod',
    'DirectoryServiceUuid',
    'DirectoryServicePartnerName',
    'EntityName',
    'EntityType',
    'EntityUuid',
    'FromIPAddress',
    'ImpersonatorUuid',
    'Level',
    'NewEntity',
    'NormalizedUser',
    'OldEntity',
    'RequestDeviceOS',
    'RequestHostName',
    'RequestIsMobileDevice',
    'Tenant',
    'UserGuid',
    'WhenLogged',
    'WhenOccurred',
    'ObjectName',
    'ObjectType',
    'RoleId',
    'Role',
    'Changer',
    'ChangerUuid',
    'Result',
    'Alias',
    'ReplaceDomain',
    'Type',
    'Id',
    'ProfileName',
    'Thumbprint',
    'TargetUserID',
    'TargetUser',
    'Uuid',
    'Key',
    'Value',
    'UserState',
    'PreviousUserState',
    'FailedMessage',
    'Exception',
    'DSType',
    'DSName',
    'DSUuid',
    'ImpersonateTargetUuid',
    'ImpersonateTargetName',
    'EmailAddress',
    'Session',
    'MfaResult',
    'MfaReason',
    'SetPath',
    'ProxyId',
    'MachineName',
    'ClientAddress',
    'ConnectorUuid',
    'HostAddress',
    'UserName',
    'Target',
    'Cname',
    'OldState',
    'NewState',
    'AffectedTenant',
    'OU',
    'DeviceID',
    'EnrollProfileUser',
    'LocalAccountUuid',
    'DeviceName',
    'FailureReason',
    'From',
    'To',
    'Description',
    'DeleteReason',
    'LicenseType',
    'NewLicenseType',
    'OldLicenseType',
    'ApplicationType',
    'ApplicationName',
    'ApplicationID',
    'MobileAppType',
    'AppId',
    'AppName',
    'JobUniqueId',
    'SyncAction',
    'SyncActionReason',
    'SyncResult',
    'SessionId',
]


class CyberArkEventsOptions(IntegrationOptions):
    app_id: str


class CyberArkEventsRequest(IntegrationHTTPRequest):
    method = Method.GET
    headers = {'Accept': '*/*', 'Content-Type': 'application/json'}


class CyberArkEventsClient(IntegrationEventsClient):
    request: IntegrationHTTPRequest
    options: CyberArkEventsOptions

    def __init__(
        self,
        request: CyberArkEventsRequest,
        options: CyberArkEventsOptions,
        credentials: Credentials,
        session=requests.Session(),
    ) -> None:
        self.access_token = None
        self.credentials = credentials
        super().__init__(request, options, session)

    def set_request_filter(self, after: Any):
        return after

    def authenticate(self):
        credentials = base64.b64encode(f'{self.credentials.identifier}:{self.credentials.password}'.encode()).decode()
        request = IntegrationHTTPRequest(
            method=self.request.method,
            url=f"{self.request.url.removesuffix('/')}/oauth2/token/{self.options.app_id}",
            headers={'Authorization': f"Basic {credentials}"},
            data={'grant_type': 'client_credentials', 'scope': 'siem'},
            verify=not self.request.verify,
        )

        response = self.call(request)
        if response.ok:
            demisto.debug('authenticated successfully')
            self.access_token = response.json()['access_token']
            self.request.headers['Authorization'] = f'Bearer {self.access_token}'
        else:
            demisto.debug(f'authentication failed: {response.json()}')


class CyberArkGetEvents(IntegrationGetEvents):
    client: CyberArkEventsClient

    @staticmethod
    def get_last_run_ids(events: list) -> list:
        return [event.get('ID') for event in events]

    @staticmethod
    def get_last_run_time(events: list) -> str:
        # The date is in timestamp format and looks like {'WhenOccurred': '/Date(1651483379362)/'}
        last_timestamp = max([int(e.get('WhenOccurred', '').removesuffix(')/').removeprefix('/Date(')) for e in events])

        return datetime.utcfromtimestamp(last_timestamp / 1000).strftime(DATETIME_FORMAT)

    def get_last_run(self, events: list) -> dict:  # type: ignore
        return {'from': self.get_last_run_time(events), 'ids': self.get_last_run_ids(events)}

    def _iter_events(self):
        self.client.authenticate()

        result = self.client.call(self.client.request).json()['Result']

        if events := result.get('Results'):
            fetched_events_ids = demisto.getLastRun().get('ids', [])
            yield [event.get('Row') for event in events if event.get('Row', {}).get('ID') not in fetched_events_ids]


def get_request_params(**kwargs: dict) -> dict:
    fetch_from = str(kwargs.get('from', '3 days'))
    default_from_day = datetime.now() - timedelta(days=3)
    from_time = datetime.strftime(dateparser.parse(fetch_from, settings={'TIMEZONE': 'UTC'}) or default_from_day, DATETIME_FORMAT)

    params = {
        'url': f'{str(kwargs.get("url", "")).removesuffix("/")}/RedRock/Query',
        'data': json.dumps({
            "Script": f"Select {', '.join(EVENT_FIELDS)} from Event where WhenOccurred > '{from_time}'"
        }),
        'verify': not kwargs.get('insecure')
    }
    return params


def main(command: str, demisto_params: dict):
    credentials = Credentials(**demisto_params.get('credentials', {}))
    options = CyberArkEventsOptions(**demisto_params)
    request_params = get_request_params(**demisto_params)
    request = CyberArkEventsRequest(**request_params)
    client = CyberArkEventsClient(request, options, credentials)
    get_events = CyberArkGetEvents(client, options)

    try:
        if command == 'test-module':
            get_events.run()
            demisto.results('ok')

        if command in ('fetch-events', 'cyberark-get-events'):
            events = get_events.run()

            if events:
                last_run = get_events.get_last_run(events)
                demisto.debug(f'Set last run to {last_run}')
                demisto.setLastRun(last_run)

            if command == 'fetch-events' or demisto_params.get('should_push_events'):
                send_events_to_xsiam(events, vendor=demisto_params.get('vendor'), product=demisto_params.get('product'))

            if command == 'cyberark-get-events':
                command_results = CommandResults(
                    readable_output=tableToMarkdown(
                        'CyberArkIdentity RedRock records', events, removeNull=True, headerTransform=pascalToSpace
                    ),
                    raw_response=events,
                )
                return_results(command_results)

    except Exception as e:
        return_error(str(e))


if __name__ in ('__main__', '__builtin__', 'builtins'):
    # Args is always stronger. Get getIntegrationContext even stronger
    demisto_params_ = demisto.params() | demisto.args() | demisto.getLastRun()
    main(demisto.command(), demisto_params_)
