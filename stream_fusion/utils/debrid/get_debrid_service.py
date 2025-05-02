from fastapi.exceptions import HTTPException

from stream_fusion.utils.debrid.alldebrid import AllDebrid
from stream_fusion.utils.debrid.realdebrid import RealDebrid
from stream_fusion.utils.debrid.torbox import Torbox
from stream_fusion.utils.debrid.premiumize import Premiumize
from stream_fusion.utils.debrid.stremthrudebrid import StremThruDebrid
from stream_fusion.logging_config import logger
from stream_fusion.settings import settings
from fastapi import Request
import aiohttp

def normalize_service_name(service_name):
    return service_name.lower().replace('-', '')

def get_all_debrid_services(config):
    services_config = config.get('service', [])
    debrid_services = []
    stremthru_enabled = config.get('stremthru_enabled', False)

    if not services_config:
        logger.error("No service configuration found in the config file.")
        return []

    if stremthru_enabled:
        logger.info("StremThru est activé (config requête). Tentative d'instanciation du client StremThru.")
        if config.get('stremthru_url'):
            try:
                debrid_services.append(StremThruDebrid(config))
                logger.info("Client StremThruDebrid instancié avec succès.")
            except Exception as e:
                logger.error(f"Erreur lors de l'instanciation de StremThruDebrid: {e}")
        else:
            logger.error("StremThru est activé mais stremthru_url manque dans la config.")

    # Instantiate direct Debrid services based on config['service']
    logger.debug("Instantiating direct Debrid services based on config['service']")
    service_map = {
        "RD": (RealDebrid, config.get('RDToken')),
        "Real-Debrid": (RealDebrid, config.get('RDToken')),
        "AD": (AllDebrid, config.get('ADToken')),
        "AllDebrid": (AllDebrid, config.get('ADToken')),
        "PM": (Premiumize, config.get('PMToken')),
        "Premiumize": (Premiumize, config.get('PMToken')),
        "TB": (Torbox, config.get('TBToken')),
        "TorBox": (Torbox, config.get('TBToken'))
    }
    direct_services_to_instantiate = [s for s in services_config if s in service_map]
    if not direct_services_to_instantiate and not debrid_services:
        logger.error("No valid direct Debrid services found and StremThru not used.")
        return []
    for service_name in direct_services_to_instantiate:
        service_class, token = service_map[service_name]
        if token:
            try:
                debrid_services.append(service_class(config))
                logger.info(f"Service {service_name} instantiated successfully.")
            except Exception as e:
                logger.error(f"Error instantiating {service_name}: {e}")
        else:
            logger.warning(f"Missing token for {service_name}; service skipped.")

    if not debrid_services:
        logger.error("Finalement, aucun service Debrid valide n'a pu être instancié.")
        return []

    unique_services = []
    seen_types = set()
    for service in debrid_services:
        if type(service) not in seen_types:
            unique_services.append(service)
            seen_types.add(type(service))

    logger.info(f"Services Debrid instanciés : {[type(s).__name__ for s in unique_services]}")
    return unique_services

def get_download_service(config):
    target_service_name = None
    if not settings.download_service:
        service_config_name = config.get('debridDownloader')
        if not service_config_name:
            enabled_services = config.get('service', [])
            if len(enabled_services) == 1:
                target_service_name = enabled_services[0]
                logger.info(f"Utilisation du seul service actif comme service de téléchargement: {target_service_name}")
            else:
                logger.error("Plusieurs services activés. Veuillez sélectionner un service de téléchargement.")
                raise HTTPException(
                    status_code=500,
                    detail="Multiple services enabled. Please select a download service in the web interface."
                )
        else:
            target_service_name = service_config_name
    else:
        target_service_name = settings.download_service

    if not target_service_name:
         logger.error("Impossible de déterminer le service de téléchargement cible.")
         raise HTTPException(status_code=500, detail="Impossible de déterminer le service de téléchargement.")

    logger.debug(f"Service de téléchargement cible déterminé: {target_service_name}")

    # Use StremThru if enabled, otherwise fall back to direct service
    if config.get("stremthru_enabled", False):
        logger.info(f"StremThru activé. Utilisation de StremThruDebrid pour '{target_service_name}'.")
        return StremThruDebrid(config)

    logger.info(f"StremThru désactivé. Utilisation du client direct pour '{target_service_name}'.")
    if target_service_name == "Real-Debrid":
        return RealDebrid(config)
    elif target_service_name == "AllDebrid":
        return AllDebrid(config)
    elif target_service_name == "TorBox":
        return Torbox(config)
    elif target_service_name == "Premiumize":
        return Premiumize(config)
    else:
        logger.error(f"Service de téléchargement invalide spécifié: {target_service_name}")
        raise HTTPException(status_code=500, detail=f"Invalid download service: {target_service_name}.")

SERVICE_MAP = {
    "RD": "Real-Debrid",
    "AD": "AllDebrid",
    "TB": "TorBox",
    "PM": "Premiumize",
    "DL": "DOWNLOAD_SERVICE"
}

def get_debrid_service(config, service_short_code, request: Request):
    target_service_name = None
    http_session = request.app.state.http_session

    if service_short_code == "DL":
         logger.debug("get_debrid_service: Délégation à get_download_service pour 'DL'.")
         return get_download_service(config)
    elif service_short_code in SERVICE_MAP:
        target_service_name = SERVICE_MAP[service_short_code]
        logger.debug(f"get_debrid_service: Service demandé '{service_short_code}' mappé à '{target_service_name}'.")
    else:
        logger.warning(f"get_debrid_service: Code de service inconnu '{service_short_code}'.")
        raise HTTPException(status_code=400, detail=f"Unknown service code: {service_short_code}")

    # Use StremThru proxy for all services if enabled
    if config.get('stremthru_enabled', False):
        logger.info(f"StremThru activé. Délégation de '{target_service_name}' au client StremThru.")
        return StremThruDebrid(config, session=http_session)

    # Fallback: direct service instantiation
    service_map = {
        "real-debrid": RealDebrid,
        "alldebrid": AllDebrid,
        "torbox": Torbox,
        "premiumize": Premiumize
    }
    normalized = normalize_service_name(target_service_name)
    if normalized in service_map:
        cls = service_map[normalized]
        return cls(config, session=http_session)
    else:
        logger.error(f"Service Debrid invalide spécifié: {target_service_name}")
        raise HTTPException(status_code=400, detail=f"Unsupported debrid service: {target_service_name}")
