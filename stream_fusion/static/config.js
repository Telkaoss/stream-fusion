const sorts = ['quality', 'sizedesc', 'sizeasc', 'qualitythensize'];
const qualityExclusions = ['2160p', '1080p', '720p', '480p', 'rips', 'cam', 'hevc', 'unknown'];
const languages = ['en', 'fr', 'multi', 'vfq'];


const implementedDebrids = ['debrid_rd', 'debrid_ad', 'debrid_tb', 'debrid_pm', 'sharewood', 'yggflix'];

const unimplementedDebrids = ['debrid_dl', 'debrid_ed', 'debrid_oc', 'debrid_pk'];

document.addEventListener('DOMContentLoaded', function () {
    loadData();
    handleUniqueAccounts();
    updateProviderFields();
    updateDebridOrderList();
    toggleStremThruFields();
    
    const apiKeyInput = document.getElementById('ApiKey');
    if (apiKeyInput) {
        apiKeyInput.addEventListener('blur', function() {
            validateApiKeyWithoutAlert(this.value);
        });
        
        if (apiKeyInput.value && apiKeyInput.value.trim() !== '') {
            validateApiKeyWithoutAlert(apiKeyInput.value);
        }
    }
});

function setElementDisplay(elementId, displayStatus) {
    const element = document.getElementById(elementId);
    if (element) {
        element.style.display = displayStatus;
    }
}

function startRealDebridAuth() {
    document.getElementById('rd-auth-button').disabled = true;
    document.getElementById('rd-auth-button').textContent = "Authentification en cours...";

    fetch('/api/auth/realdebrid/device_code', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json'
        },
        body: JSON.stringify({})
    })
        .then(response => {
            if (!response.ok) {
                throw new Error('Erreur de requête');
            }
            return response.json();
        })
        .then(data => {
            document.getElementById('verification-url').href = data.direct_verification_url;
            document.getElementById('verification-url').textContent = data.verification_url;
            document.getElementById('user-code').textContent = data.user_code;
            document.getElementById('auth-instructions').style.display = 'block';
            pollForCredentials(data.device_code, data.expires_in);
        })
        .catch(error => {
            alert("Erreur lors de l'authentification. Veuillez réessayer.");
            resetAuthButton();
        });
}

function pollForCredentials(deviceCode, expiresIn) {
    const pollInterval = setInterval(() => {
        fetch(`/api/auth/realdebrid/credentials?device_code=${encodeURIComponent(deviceCode)}`, {
            method: 'POST',
            headers: {
                'accept': 'application/json'
            }
        })
            .then(response => {
                if (!response.ok) {
                    if (response.status === 400) {
                        console.log('Autorisation en attente...');
                        return null;
                    }
                    throw new Error('Erreur de requête');
                }
                return response.json();
            })
            .then(data => {
                if (data && data.client_id && data.client_secret) {
                    clearInterval(pollInterval);
                    clearTimeout(timeoutId);
                    getToken(deviceCode, data.client_id, data.client_secret);
                }
            })
            .catch(error => {
                console.error('Erreur:', error);
                console.log('Tentative suivante dans 5 secondes...');
            });
    }, 5000);

    const timeoutId = setTimeout(() => {
        clearInterval(pollInterval);
        alert("Le délai d'authentification a expiré. Veuillez réessayer.");
        resetAuthButton();
    }, expiresIn * 1000);
}

function getToken(deviceCode, clientId, clientSecret) {
    const url = `/api/auth/realdebrid/token?client_id=${encodeURIComponent(clientId)}&client_secret=${encodeURIComponent(clientSecret)}&device_code=${encodeURIComponent(deviceCode)}`;

    fetch(url, {
        method: 'POST',
        headers: {
            'accept': 'application/json'
        }
    })
        .then(response => {
            if (!response.ok) {
                throw new Error('Erreur de requête');
            }
            return response.json();
        })
        .then(data => {
            if (data.access_token && data.refresh_token) {
                const rdCredentials = {
                    client_id: clientId,
                    client_secret: clientSecret,
                    access_token: data.access_token,
                    refresh_token: data.refresh_token
                };
                document.getElementById('rd_token_info').value = JSON.stringify(rdCredentials, null, 2);
                document.getElementById('auth-status').style.display = 'block';
                document.getElementById('auth-instructions').style.display = 'none';
                document.getElementById('rd-auth-button').disabled = true;
                document.getElementById('rd-auth-button').classList.add('opacity-50', 'cursor-not-allowed');
                document.getElementById('rd-auth-button').textContent = "Connexion réussie";
            } else {
                throw new Error('Tokens non reçus');
            }
        })
        .catch(error => {
            console.error('Erreur:', error);
            console.log('Erreur lors de la récupération du token. Nouvelle tentative lors du prochain polling.');
        });
}

function resetAuthButton() {
    const button = document.getElementById('rd-auth-button');
    button.disabled = false;
    button.textContent = "S'authentifier avec Real-Debrid";
    button.classList.remove('opacity-50', 'cursor-not-allowed');
}

function startADAuth() {
    document.getElementById('ad-auth-button').disabled = true;
    document.getElementById('ad-auth-button').textContent = "Authentication in progress...";

    console.log('Starting AllDebrid authentication');
    fetch('/api/auth/alldebrid/pin/get', {
        method: 'GET',
        headers: {
            'Content-Type': 'application/json'
        }
    })
        .then(response => {
            console.log('Response received', response);
            if (!response.ok) {
                throw new Error('Request error');
            }
            return response.json();
        })
        .then(data => {
            document.getElementById('ad-verification-url').href = data.data.user_url;
            document.getElementById('ad-verification-url').textContent = data.data.base_url;
            document.getElementById('ad-user-code').textContent = data.data.pin;
            document.getElementById('ad-auth-instructions').style.display = 'block';
            pollForADCredentials(data.data.check, data.data.pin, data.data.expires_in);
        })
        .catch(error => {
            console.error('Detailed error:', error);
            alert("Authentication error. Please try again.");
            resetADAuthButton();
        });
}

function pollForADCredentials(check, pin, expiresIn) {
    const pollInterval = setInterval(() => {
        fetch(`/api/auth/alldebrid/pin/check?agent=streamfusion&check=${encodeURIComponent(check)}&pin=${encodeURIComponent(pin)}`, {
            method: 'GET',
            headers: {
                'accept': 'application/json'
            }
        })
            .then(response => {
                if (response.status === 400) {
                    console.log('Waiting for user authorization...');
                    return null;
                }
                if (!response.ok) {
                    throw new Error('Request error');
                }
                return response.json();
            })
            .then(data => {
                if (data === null) return;
                if (data.data && data.data.activated && data.data.apikey) {
                    clearInterval(pollInterval);
                    clearTimeout(timeoutId);
                    document.getElementById('ad_token_info').value = data.data.apikey;
                    document.getElementById('ad-auth-status').style.display = 'block';
                    document.getElementById('ad-auth-instructions').style.display = 'none';
                    document.getElementById('ad-auth-button').disabled = true;
                    document.getElementById('ad-auth-button').textContent = "Connection successful";
                    console.log('AllDebrid authentication successful');
                } else {
                    console.log('Waiting for user authorization...');
                }
            })
            .catch(error => {
                console.error('Error:', error);
                console.log('Next attempt in 5 seconds...');
            });
    }, 5000);

    const timeoutId = setTimeout(() => {
        clearInterval(pollInterval);
        alert("Authentication timeout. Please try again.");
        resetADAuthButton();
    }, expiresIn * 1000);
}

function resetADAuthButton() {
    const button = document.getElementById('ad-auth-button');
    button.disabled = false;
    button.textContent = "Connect with AllDebrid";
}

function handleUniqueAccounts() {
    const accounts = ['debrid_rd', 'debrid_ad', 'debrid_tb', 'debrid_pm', 'sharewood', 'yggflix'];

    accounts.forEach(account => {
        const checkbox = document.getElementById(account);
        if (checkbox) {
            const isUnique = checkbox.dataset.uniqueAccount === 'true';
            if (!isUnique) {
            } else {
                checkbox.checked = isUnique;
                checkbox.disabled = isUnique;
                checkbox.parentElement.classList.add('opacity-50', 'cursor-not-allowed');
            }
        }
    });
}

function updateDebridOrderList() {
    const debridOrderList = document.getElementById('debridOrderList');
    if (!debridOrderList) return;

    debridOrderList.innerHTML = '';

    let debridOrder = [];
    const currentUrl = window.location.href;
    let data = currentUrl.match(/\/([^\/]+)\/configure$/);
    if (data && data[1]) {
        try {
            const decodedData = JSON.parse(atob(data[1]));
            debridOrder = decodedData.service || [];
        } catch (error) {
            console.warn("No valid debrid order data in URL, using default order.");
        }
    }

    const rdEnabled = document.getElementById('debrid_rd').checked || document.getElementById('debrid_rd').disabled;
    const adEnabled = document.getElementById('debrid_ad').checked || document.getElementById('debrid_ad').disabled;
    const tbEnabled = document.getElementById('debrid_tb').checked || document.getElementById('debrid_tb').disabled;
    const pmEnabled = document.getElementById('debrid_pm').checked || document.getElementById('debrid_pm').disabled;
    const dlEnabled = document.getElementById('debrid_dl')?.checked || document.getElementById('debrid_dl')?.disabled;
    const edEnabled = document.getElementById('debrid_ed')?.checked || document.getElementById('debrid_ed')?.disabled;
    const ocEnabled = document.getElementById('debrid_oc')?.checked || document.getElementById('debrid_oc')?.disabled;
    const pkEnabled = document.getElementById('debrid_pk')?.checked || document.getElementById('debrid_pk')?.disabled;

    if (debridOrder.length === 0 ||
        !debridOrder.every(service =>
            (service === 'Real-Debrid' && rdEnabled) ||
            (service === 'AllDebrid' && adEnabled) ||
            (service === 'TorBox' && tbEnabled) ||
            (service === 'Premiumize' && pmEnabled) ||
            (service === 'Debrid-Link' && dlEnabled) ||
            (service === 'EasyDebrid' && edEnabled) ||
            (service === 'Offcloud' && ocEnabled) ||
            (service === 'PikPak' && pkEnabled)
        )) {
        debridOrder = [];
        if (rdEnabled) debridOrder.push('Real-Debrid');
        if (adEnabled) debridOrder.push('AllDebrid');
        if (tbEnabled) debridOrder.push('TorBox');
        if (pmEnabled) debridOrder.push('Premiumize');
        if (dlEnabled) debridOrder.push('Debrid-Link');
        if (edEnabled) debridOrder.push('EasyDebrid');
        if (ocEnabled) debridOrder.push('Offcloud');
        if (pkEnabled) debridOrder.push('PikPak');
    }

    debridOrder.forEach(serviceName => {
        if ((serviceName === 'Real-Debrid' && rdEnabled) ||
            (serviceName === 'AllDebrid' && adEnabled) ||
            (serviceName === 'Premiumize' && pmEnabled) ||
            (serviceName === 'TorBox' && tbEnabled) ||
            (serviceName === 'Debrid-Link' && dlEnabled) ||
            (serviceName === 'EasyDebrid' && edEnabled) ||
            (serviceName === 'Offcloud' && ocEnabled) ||
            (serviceName === 'PikPak' && pkEnabled)) {
            addDebridToList(serviceName);
        }
    });

    if (rdEnabled && !debridOrder.includes('Real-Debrid')) {
        addDebridToList('Real-Debrid');
    }
    if (adEnabled && !debridOrder.includes('AllDebrid')) {
        addDebridToList('AllDebrid');
    }
    if (tbEnabled && !debridOrder.includes('TorBox')) {
        addDebridToList('TorBox');
    }
    if (pmEnabled && !debridOrder.includes('Premiumize')) {
        addDebridToList('Premiumize');
    }
    if (dlEnabled && !debridOrder.includes('Debrid-Link')) {
        addDebridToList('Debrid-Link');
    }
    if (edEnabled && !debridOrder.includes('EasyDebrid')) {
        addDebridToList('EasyDebrid');
    }
    if (ocEnabled && !debridOrder.includes('Offcloud')) {
        addDebridToList('Offcloud');
    }
    if (pkEnabled && !debridOrder.includes('PikPak')) {
        addDebridToList('PikPak');
    }

    Sortable.create(debridOrderList, {
        animation: 150,
        ghostClass: 'bg-gray-100',
        onEnd: function () {
            const newOrder = Array.from(debridOrderList.children).map(li => li.dataset.serviceName);
            console.log("Nouvel ordre des débrideurs:", newOrder);
        }
    });
}


function addDebridToList(serviceName) {
    const debridOrderList = document.getElementById('debridOrderList');
    const li = document.createElement('li');
    li.className = 'bg-gray-700 text-white text-sm p-1.5 rounded shadow cursor-move flex items-center justify-between w-64 mb-2';

    const text = document.createElement('span');
    text.textContent = serviceName;
    text.className = 'flex-grow truncate';

    const icon = document.createElement('span');
    icon.innerHTML = '&#8942;';
    icon.className = 'text-gray-400 ml-2 flex-shrink-0';

    li.appendChild(text);
    li.appendChild(icon);
    li.dataset.serviceName = serviceName;
    debridOrderList.appendChild(li);
}

function toggleDebridOrderList() {
    const orderList = document.getElementById('debridOrderList');
    const isChecked = document.getElementById('debrid_order').checked;
    orderList.classList.toggle('hidden', !isChecked);

    if (isChecked) {
        updateDebridOrderList();
    }
}

function toggleStremThruFields() {
    const stremthruEnabledCheckbox = document.getElementById('stremthru_enabled');
    if (!stremthruEnabledCheckbox) return;
    
    const isEnabled = stremthruEnabledCheckbox.checked;
    const urlDiv = document.getElementById('stremthru_url_div');
    const authDiv = document.getElementById('stremthru_auth_div');
    const urlInput = document.getElementById('stremthru_url');
    const defaultUrl = 'https://stremthru.13377001.xyz/';

    if (isEnabled) {
        setElementDisplay('stremthru_url_div', 'block');
        if (authDiv) setElementDisplay('stremthru_auth_div', 'block');
        
        // Set default URL if empty or placeholder
        if (urlInput && (!urlInput.value || urlInput.value === urlInput.placeholder)) {
            urlInput.value = defaultUrl;
        }
    } else {
        setElementDisplay('stremthru_url_div', 'none');
        if (authDiv) setElementDisplay('stremthru_auth_div', 'none');

        // Si StremThru est désactivé, désactiver et décocher les services non implémentés
        unimplementedDebrids.forEach(id => {
            const checkbox = document.getElementById(id);
            if (checkbox && checkbox.checked) {
                checkbox.checked = false;
                // Déclencher manuellement la mise à jour pour masquer les champs
                updateProviderFields(); 
            }
        });
    }
}

function updateDebridDownloaderOptions() {
    const debridDownloaderOptions = document.getElementById('debridDownloaderOptions');
    if (!debridDownloaderOptions) return;

    debridDownloaderOptions.innerHTML = '';

    // --- Vérifier les services de débridage standard ---
    const rdEnabled = document.getElementById('debrid_rd')?.checked || document.getElementById('debrid_rd')?.disabled;
    const adEnabled = document.getElementById('debrid_ad')?.checked || document.getElementById('debrid_ad')?.disabled;
    const tbEnabled = document.getElementById('debrid_tb')?.checked || document.getElementById('debrid_tb')?.disabled;
    const pmEnabled = document.getElementById('debrid_pm')?.checked || document.getElementById('debrid_pm')?.disabled;
    const dlEnabled = document.getElementById('debrid_dl')?.checked || document.getElementById('debrid_dl')?.disabled;
    const edEnabled = document.getElementById('debrid_ed')?.checked || document.getElementById('debrid_ed')?.disabled;
    const ocEnabled = document.getElementById('debrid_oc')?.checked || document.getElementById('debrid_oc')?.disabled;
    const pkEnabled = document.getElementById('debrid_pk')?.checked || document.getElementById('debrid_pk')?.disabled;
    
    // --- Vérifier StremThru --- 
    const stremthruEnabledCheckbox = document.getElementById('stremthru_enabled');
    const stremthruEnabled = stremthruEnabledCheckbox ? stremthruEnabledCheckbox.checked : false;

    let firstOption = null;

    // --- Ajouter des options en fonction des services activés ---
    if (rdEnabled) {
        firstOption = addDebridDownloaderOption('Real-Debrid');
    }
    if (adEnabled) {
        // Utiliser l'opérateur ternaire pour une attribution plus propre
        firstOption = firstOption ? firstOption : addDebridDownloaderOption('AllDebrid'); 
        if (firstOption.value !== 'AllDebrid') addDebridDownloaderOption('AllDebrid');
    }
    if (tbEnabled) {
        firstOption = firstOption ? firstOption : addDebridDownloaderOption('TorBox');
        if (firstOption.value !== 'TorBox') addDebridDownloaderOption('TorBox');
    }
    if (pmEnabled) {
        firstOption = firstOption ? firstOption : addDebridDownloaderOption('Premiumize');
        if (firstOption.value !== 'Premiumize') addDebridDownloaderOption('Premiumize');
    }
    if (dlEnabled) {
        firstOption = firstOption ? firstOption : addDebridDownloaderOption('Debrid-Link');
        if (firstOption.value !== 'Debrid-Link') addDebridDownloaderOption('Debrid-Link');
    }
    if (edEnabled) {
        firstOption = firstOption ? firstOption : addDebridDownloaderOption('EasyDebrid');
        if (firstOption.value !== 'EasyDebrid') addDebridDownloaderOption('EasyDebrid');
    }
    if (ocEnabled) {
        firstOption = firstOption ? firstOption : addDebridDownloaderOption('Offcloud');
        if (firstOption.value !== 'Offcloud') addDebridDownloaderOption('Offcloud');
    }
    if (pkEnabled) {
        firstOption = firstOption ? firstOption : addDebridDownloaderOption('PikPak');
        if (firstOption.value !== 'PikPak') addDebridDownloaderOption('PikPak');
    }
    
    // --- Ajouter StremThru si activé --- 
    if (stremthruEnabled) {
        firstOption = firstOption ? firstOption : addDebridDownloaderOption('StremThru');
        if (firstOption.value !== 'StremThru') addDebridDownloaderOption('StremThru');
    }

    // Sélectionner la première option ajoutée par défaut si aucune n'est sélectionnée
    if (firstOption && !document.querySelector('input[name="debrid_downloader"]:checked')) {
        firstOption.checked = true;
    }
}

function addDebridDownloaderOption(serviceName) {
    const debridDownloaderOptions = document.getElementById('debridDownloaderOptions');
    const id = `debrid_downloader_${serviceName.toLowerCase().replace('-', '_')}`;

    const div = document.createElement('div');
    div.className = 'flex items-center';

    const input = document.createElement('input');
    input.type = 'radio';
    input.id = id;
    input.name = 'debrid_downloader';
    input.value = serviceName;
    input.className = 'h-4 w-4 border-gray-300 text-indigo-600 focus:ring-indigo-600';

    const label = document.createElement('label');
    label.htmlFor = id;
    label.className = 'ml-3 block text-sm font-medium text-white';
    label.textContent = serviceName;

    div.appendChild(input);
    div.appendChild(label);
    debridDownloaderOptions.appendChild(div);

    return input;
}


function updateProviderFields() {
    console.log("--- Running updateProviderFields ---"); // Debug start
    const stremthruEnabledCheckbox = document.getElementById('stremthru_enabled');
    let stremthruWasEnabled = stremthruEnabledCheckbox ? stremthruEnabledCheckbox.checked : false; // Track initial state
    let stremthruForcedEnable = false;
    let anyUnimplementedChecked = false; // Flag to track if any unimplemented service is checked

    const serviceStates = {};
    const allDebrids = [...implementedDebrids, ...unimplementedDebrids];

    // Vérifier l'état des autres éléments de l'interface
    const cacheChecked = document.getElementById('cache')?.checked;
    const yggflixChecked = document.getElementById('yggflix')?.checked || document.getElementById('yggflix')?.disabled;
    const sharewoodChecked = document.getElementById('sharewood')?.checked || document.getElementById('sharewood')?.disabled;
    const torboxChecked = document.getElementById('debrid_tb')?.checked || document.getElementById('debrid_tb')?.disabled;

    // Afficher/masquer les champs spécifiques
    setElementDisplay('cache-fields', cacheChecked ? 'block' : 'none');
    setElementDisplay('ygg-fields', yggflixChecked ? 'block' : 'none');
    setElementDisplay('sharewood-fields', sharewoodChecked ? 'block' : 'none');
    setElementDisplay('tb_debrid-fields', torboxChecked ? 'block' : 'none');

    // Traiter tous les débrideurs
    allDebrids.forEach(id => {
        const checkbox = document.getElementById(id);
        if (!checkbox) return;
        const isChecked = checkbox.checked || checkbox.disabled;
        serviceStates[id] = isChecked;

        // Déterminer l'ID du div de credentials correspondant
        let credDivId = ''; 
        switch (id) {
            case 'debrid_rd': credDivId = 'rd_token_info_div'; break;
            case 'debrid_ad': credDivId = 'ad_token_info_div'; break;
            case 'debrid_pm': credDivId = 'pm_token_info_div'; break;
            case 'debrid_tb': credDivId = 'tb_token_info_div'; break;
            case 'debrid_dl': credDivId = 'debridlink_api_key_div'; break;
            case 'debrid_ed': credDivId = 'easydebrid_api_key_div'; break;
            case 'debrid_oc': credDivId = 'offcloud_credentials_div'; break;
            case 'debrid_pk': credDivId = 'pikpak_credentials_div'; break;
        }

        // Afficher/masquer le div de credentials
        if (credDivId) {
            setElementDisplay(credDivId, isChecked ? 'block' : 'none');
        }

        // Logique pour forcer l'activation de StremThru avec les débrideurs non implémentés
        if (unimplementedDebrids.includes(id) && isChecked) {
            if (stremthruEnabledCheckbox && !stremthruEnabledCheckbox.checked) {
                stremthruEnabledCheckbox.checked = true;
                stremthruForcedEnable = true; // Marquer qu'on l'a forcé
            }
            anyUnimplementedChecked = true; // Définir le drapeau
        }
    });

    // Gérer l'état de la case à cocher StremThru: désactiver si un service non implémenté est coché
    if (stremthruEnabledCheckbox) {
        if (anyUnimplementedChecked) {
            stremthruEnabledCheckbox.checked = true; // S'assurer qu'elle est cochée
            stremthruEnabledCheckbox.disabled = true; // Désactiver la case à cocher
        } else {
            stremthruEnabledCheckbox.disabled = false; // Réactiver si aucun service non implémenté n'est coché
        }

        // Afficher/masquer les champs StremThru
        setElementDisplay('stremthru_url_div', stremthruEnabledCheckbox.checked ? 'block' : 'none');
        const authDiv = document.getElementById('stremthru_auth_div');
        if (authDiv) {
            setElementDisplay('stremthru_auth_div', stremthruEnabledCheckbox.checked ? 'block' : 'none');
        }
    }

    // Si on a forcé l'activation de StremThru OU si son état a changé, mettre à jour la visibilité de ses champs
    if (stremthruEnabledCheckbox && (stremthruForcedEnable || stremthruEnabledCheckbox.checked !== stremthruWasEnabled || anyUnimplementedChecked)) {
        toggleStremThruFields();
    }

    // Gérer l'ordre des débrideurs
    const debridOrderCheckbox = document.getElementById('debrid_order');
    const debridOrderList = document.getElementById('debridOrderList');

    if (debridOrderCheckbox && debridOrderList) {
        // Vérifier si au moins un débrideur est activé
        const anyDebridEnabled = Object.values(serviceStates).some(state => state);

        debridOrderCheckbox.disabled = !anyDebridEnabled;
        
        if (!anyDebridEnabled) {
            debridOrderCheckbox.checked = false;
        }

        debridOrderList.classList.toggle('hidden', !(anyDebridEnabled && debridOrderCheckbox.checked));
    }

    // Mettre à jour les listes et options
    updateDebridOrderList();
    updateDebridDownloaderOptions();
    ensureDebridConsistency();
    console.log("--- Finished updateProviderFields ---"); // Debug end
}

function ensureDebridConsistency() {
    // Récupérer l'état de tous les débrideurs
    const serviceStates = {};
    const allDebrids = [...implementedDebrids, ...unimplementedDebrids];
    let anyDebridChecked = false;
    let anyUnimplementedChecked = false;

    allDebrids.forEach(id => {
        const checkbox = document.getElementById(id);
        if (!checkbox) return;
        const isChecked = checkbox.checked;
        serviceStates[id] = isChecked;
        if (isChecked) {
            anyDebridChecked = true;
            if (unimplementedDebrids.includes(id)) {
                anyUnimplementedChecked = true;
            }
        }
    });

    // Gérer l'état de la case à cocher d'ordre des débrideurs
    const debridOrderCheckbox = document.getElementById('debrid_order');
    const debridOrderList = document.getElementById('debridOrderList');
    
    if (debridOrderCheckbox && debridOrderList) {
        if (!anyDebridChecked) {
            debridOrderCheckbox.checked = false;
            debridOrderList.classList.add('hidden');
        }

        if (debridOrderCheckbox.checked && !anyDebridChecked) {
            debridOrderCheckbox.checked = false;
        }
    }

    // Gérer l'état de la case à cocher StremThru
    const stremthruEnabledCheckbox = document.getElementById('stremthru_enabled');
    if (stremthruEnabledCheckbox) {
        if (anyUnimplementedChecked) {
            stremthruEnabledCheckbox.checked = true;
            stremthruEnabledCheckbox.disabled = true;
        } else {
            stremthruEnabledCheckbox.disabled = false;
        }
    }

    updateDebridDownloaderOptions();
}

function loadData() {
    const currentUrl = window.location.href;
    let data = currentUrl.match(/\/([^\/]+)\/configure$/);
    let decodedData = {};
    if (data && data[1]) {
        try {
            decodedData = JSON.parse(atob(data[1]));
        } catch (error) {
            console.warn("No valid data to decode in URL, using default values.");
        }
    }

    function setElementValue(id, value, defaultValue) {
        const element = document.getElementById(id);
        if (element) {
            if (element.type === 'radio' || element.type === 'checkbox') {
                element.checked = (value !== undefined) ? value : defaultValue;
            } else {
                element.value = value || defaultValue || '';
            }
        }
    }

    const defaultConfig = {
        jackett: false,
        cache: true,
        cacheUrl: 'https://stremio-jackett-cacher.elfhosted.com/',
        zilean: true,
        yggflix: true,
        sharewood: false,
        maxSize: '18',
        resultsPerQuality: '10',
        maxResults: '30',
        minCachedResults: '10',
        torrenting: false,
        ctg_yggtorrent: true,
        ctg_yggflix: false,
        metadataProvider: 'tmdb',
        sort: 'quality',
        exclusion: ['cam'],
        languages: ['fr', 'multi'],
        debrid_rd: false,
        debrid_ad: false,
        debrid_tb: false,
        debrid_pm: false,
        tb_usenet: false,
        tb_search: false,
        debrid_order: false
    };

    Object.keys(defaultConfig).forEach(key => {
        const value = decodedData[key] !== undefined ? decodedData[key] : defaultConfig[key];
        if (key === 'metadataProvider') {
            setElementValue('tmdb', value === 'tmdb', true);
            setElementValue('cinemeta', value === 'cinemeta', false);
        } else if (key === 'sort') {
            sorts.forEach(sort => {
                setElementValue(sort, value === sort, sort === defaultConfig.sort);
            });
        } else if (key === 'exclusion') {
            qualityExclusions.forEach(quality => {
                setElementValue(quality, value.includes(quality), defaultConfig.exclusion.includes(quality));
            });
        } else if (key === 'languages') {
            languages.forEach(language => {
                setElementValue(language, value.includes(language), defaultConfig.languages.includes(language));
            });
        } else {
            setElementValue(key, value, defaultConfig[key]);
        }
    });

    const serviceArray = decodedData.service || [];
    setElementValue('debrid_rd', serviceArray.includes('Real-Debrid'), defaultConfig.debrid_rd);
    setElementValue('debrid_ad', serviceArray.includes('AllDebrid'), defaultConfig.debrid_ad);
    setElementValue('debrid_tb', serviceArray.includes('TorBox'), defaultConfig.debrid_tb);
    setElementValue('debrid_pm', serviceArray.includes('Premiumize'), defaultConfig.debrid_pm);
    setElementValue('debrid_order', serviceArray.length > 0, defaultConfig.debrid_order);
    
    setElementValue('ctg_yggtorrent', decodedData.yggtorrentCtg, defaultConfig.ctg_yggtorrent);
    setElementValue('ctg_yggflix', decodedData.yggflixCtg, defaultConfig.ctg_yggflix);
    
    setElementValue('rd_token_info', decodedData.RDToken, '');
    setElementValue('ad_token_info', decodedData.ADToken, '');
    setElementValue('tb_token_info', decodedData.TBToken, '');
    setElementValue('pm_token_info', decodedData.PMToken, '');
    setElementValue('sharewoodPasskey', decodedData.sharewoodPasskey, '');
    setElementValue('yggPasskey', decodedData.yggPasskey, '');
    setElementValue('ApiKey', decodedData.apiKey, '');
    setElementValue('exclusion-keywords', (decodedData.exclusionKeywords || []).join(', '), '');
    
    setElementValue('tb_usenet', decodedData.TBUsenet, defaultConfig.tb_usenet);
    setElementValue('tb_search', decodedData.TBSearch, defaultConfig.tb_search);

    handleUniqueAccounts();
    updateProviderFields();

    const debridDownloader = decodedData.debridDownloader;
    if (debridDownloader) {
        const radioButton = document.querySelector(`input[name="debrid_downloader"][value="${debridDownloader}"]`);
        if (radioButton) {
            radioButton.checked = true;
        }
    }

    updateDebridDownloaderOptions();
    updateDebridOrderList();
    ensureDebridConsistency();
}

// Fonction pour valider l'API key
function validateApiKey(apiKey) {
    // Référence à l'élément d'erreur
    const apiKeyErrorElement = document.getElementById('apiKeyError');
    
    // Si aucune API key n'est fournie
    if (!apiKey || apiKey.trim() === '') {
        if (apiKeyErrorElement) {
            apiKeyErrorElement.classList.remove('hidden');
        }
        alert('Veuillez fournir une API Key Stream Fusion. Utilisez le bot Telegram : t.me/Stremiofr_bot - Envoyez la commande /generate');
        return false;
    }
    
    // Vérification du format UUID v4
    const isValidFormat = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/.test(apiKey);
    
    if (!isValidFormat) {
        if (apiKeyErrorElement) {
            apiKeyErrorElement.classList.remove('hidden');
        }
        alert('API Key invalide. Utilisez le bot Telegram : t.me/Stremiofr_bot - Envoyez la commande /generate');
        return false;
    }
    
    // Si l'API key est valide, masquer le message d'erreur
    if (apiKeyErrorElement) {
        apiKeyErrorElement.classList.add('hidden');
    }
    
    return true;
}

function getLink(method) {
    const apiKey = document.getElementById('ApiKey').value;
    
    // Vérifier l'API key en premier
    if (!validateApiKey(apiKey)) {
        return false;
    }
    
    const data = {
        addonHost: new URL(window.location.href).origin,
        apiKey: apiKey,
        service: [],
        RDToken: document.getElementById('rd_token_info')?.value,
        ADToken: document.getElementById('ad_token_info')?.value,
        TBToken: document.getElementById('tb_token_info')?.value,
        PMToken: document.getElementById('pm_token_info')?.value,
        TBUsenet: document.getElementById('tb_usenet')?.checked,
        TBSearch: document.getElementById('tb_search')?.checked,
        sharewoodPasskey: document.getElementById('sharewoodPasskey')?.value,
        maxSize: parseInt(document.getElementById('maxSize').value) || 16,
        exclusionKeywords: document.getElementById('exclusion-keywords').value.split(',').map(keyword => keyword.trim()).filter(keyword => keyword !== ''),
        languages: languages.filter(lang => document.getElementById(lang).checked),
        sort: sorts.find(sort => document.getElementById(sort).checked),
        resultsPerQuality: parseInt(document.getElementById('resultsPerQuality').value) || 5,
        maxResults: parseInt(document.getElementById('maxResults').value) || 5,
        minCachedResults: parseInt(document.getElementById('minCachedResults').value) || 5,
        exclusion: qualityExclusions.filter(quality => document.getElementById(quality).checked),
        cacheUrl: document.getElementById('cacheUrl')?.value,
        jackett: document.getElementById('jackett')?.checked,
        cache: document.getElementById('cache')?.checked,
        zilean: document.getElementById('zilean')?.checked,
        yggflix: document.getElementById('yggflix')?.checked,
        sharewood: document.getElementById('sharewood')?.checked,
        yggtorrentCtg: document.getElementById('ctg_yggtorrent')?.checked,
        yggflixCtg: document.getElementById('ctg_yggflix')?.checked,
        yggPasskey: document.getElementById('yggPasskey')?.value,
        torrenting: document.getElementById('torrenting').checked,
        debrid: false,
        metadataProvider: document.getElementById('tmdb').checked ? 'tmdb' : 'cinemeta',
        debridDownloader: document.querySelector('input[name="debrid_downloader"]:checked')?.value,
        // StremThru configuration
        stremthru: document.getElementById('stremthru_enabled')?.checked || false,
        stremthruUrl: document.getElementById('stremthru_url')?.value || 'https://stremthru.13377001.xyz',
        // Nouveaux débrideurs
        debridlinkApiKey: document.getElementById('debridlink_api_key')?.value || '',
        easydebridApiKey: document.getElementById('easydebrid_api_key')?.value || '',
        offcloudCredentials: document.getElementById('offcloud_credentials')?.value || '',
        pikpakCredentials: document.getElementById('pikpak_credentials')?.value || ''
    };

    data.service = Array.from(document.getElementById('debridOrderList').children).map(li => li.dataset.serviceName);
    data.debrid = data.service.length > 0;

    const missingRequiredFields = [];

    if (data.cache && !data.cacheUrl) missingRequiredFields.push("Cache URL");
    if (data.service.includes('Real-Debrid') && document.getElementById('rd_token_info') && !data.RDToken) missingRequiredFields.push("Real-Debrid Account Connection");
    if (data.service.includes('AllDebrid') && document.getElementById('ad_token_info') && !data.ADToken) missingRequiredFields.push("AllDebrid Account Connection");
    if (data.service.includes('TorBox') && document.getElementById('tb_token_info') && !data.TBToken) missingRequiredFields.push("TorBox Account Connection");
    if (data.service.includes('Premiumize') && document.getElementById('pm_token_info') && !data.PMToken) missingRequiredFields.push("Premiumize Account Connection");
    if (data.service.includes('Debrid-Link') && document.getElementById('debridlink_api_key') && !data.debridlinkApiKey) missingRequiredFields.push("Debrid-Link API Key");
    if (data.service.includes('EasyDebrid') && document.getElementById('easydebrid_api_key') && !data.easydebridApiKey) missingRequiredFields.push("EasyDebrid API Key");
    if (data.service.includes('Offcloud') && document.getElementById('offcloud_credentials') && !data.offcloudCredentials) missingRequiredFields.push("Offcloud Credentials");
    if (data.service.includes('PikPak') && document.getElementById('pikpak_credentials') && !data.pikpakCredentials) missingRequiredFields.push("PikPak Credentials");
    if (data.languages.length === 0) missingRequiredFields.push("Languages");
    if (data.yggflix && document.getElementById('yggPasskey') && !data.yggPasskey) missingRequiredFields.push("Ygg Passkey");
    if (data.sharewood && document.getElementById('sharewoodPasskey') && !data.sharewoodPasskey) missingRequiredFields.push("Sharewood Passkey");
    if (data.stremthru && !data.stremthruUrl) missingRequiredFields.push("StremThru URL");

    if (missingRequiredFields.length > 0) {
        alert(`Please fill all required fields: ${missingRequiredFields.join(", ")}`);
        return false;
    }

    function validatePasskey(passkey) {
        return /^[a-zA-Z0-9]{32}$/.test(passkey);
    }

    if (data.yggflix && data.yggPasskey && !validatePasskey(data.yggPasskey)) {
        alert('Ygg Passkey doit contenir exactement 32 caractères alphanumériques');
        return false;
    }

    if (data.sharewood && data.sharewoodPasskey && !validatePasskey(data.sharewoodPasskey)) {
        alert('Sharewood Passkey doit contenir exactement 32 caractères alphanumériques');
        return false;
    }

    const encodedData = btoa(JSON.stringify(data));
    const stremio_link = `${window.location.host}/${encodedData}/manifest.json`;

    if (method === 'link') {
        window.open(`stremio://${stremio_link}`, "_blank");
    } else if (method === 'copy') {
        const link = window.location.protocol + '//' + stremio_link;
        navigator.clipboard.writeText(link).then(() => {
            alert('Link copied to clipboard');
        }, () => {
            alert('Error copying link to clipboard');
        });
    }
}

let showLanguageCheckBoxes = true;
function showCheckboxes() {
    let checkboxes = document.getElementById("languageCheckBoxes");
    checkboxes.style.display = showLanguageCheckBoxes ? "block" : "none";
    showLanguageCheckBoxes = !showLanguageCheckBoxes;
}

// Fonction pour valider l'API key sans afficher d'alert
function validateApiKeyWithoutAlert(apiKey) {
    const apiKeyErrorElement = document.getElementById('apiKeyError');
    
    // Si aucune API key n'est fournie
    if (!apiKey || apiKey.trim() === '') {
        if (apiKeyErrorElement) {
            apiKeyErrorElement.classList.remove('hidden');
        }
        return false;
    }
    
    // Vérification du format UUID v4
    const isValidFormat = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/.test(apiKey);
    
    if (!isValidFormat) {
        if (apiKeyErrorElement) {
            apiKeyErrorElement.classList.remove('hidden');
        }
        return false;
    }
    
    // Si l'API key est valide, masquer le message d'erreur
    if (apiKeyErrorElement) {
        apiKeyErrorElement.classList.add('hidden');
    }
    
    return true;
}
