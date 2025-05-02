const sorts = ['quality', 'sizedesc', 'sizeasc', 'qualitythensize'];
const qualityExclusions = ['2160p', '1080p', '720p', '480p', 'rips', 'cam', 'hevc', 'unknown'];
const languages = ['en', 'fr', 'multi'];

const implementedDebrids = ['debrid_rd', 'debrid_ad', 'debrid_tb', 'debrid_pm', 'sharewood', 'yggflix'];

const unimplementedDebrids = ['debrid_dl', 'debrid_ed', 'debrid_oc', 'debrid_pk'];

document.addEventListener('DOMContentLoaded', function () {
    loadData();
    handleUniqueAccounts();
    updateProviderFields();
    updateDebridOrderList();
    toggleDebridOrderList(); // Ensure this is called to init visibility
    updateDebridDownloaderOptions(); // Ensure this is called to init visibility
    toggleStremThruFields(); // Add this line to initialize StremThru fields visibility
});

function setElementDisplay(elementId, displayStatus) {
    const element = document.getElementById(elementId);
    if (element) {
        element.style.display = displayStatus;
    }
}

function toggleStremThruFields() {
    const stremthruEnabledCheckbox = document.getElementById('stremthru_enabled');
    const isEnabled = stremthruEnabledCheckbox.checked;
    const urlDiv = document.getElementById('stremthru_url_div');
    const authDiv = document.getElementById('stremthru_auth_div');
    const urlInput = document.getElementById('stremthru_url');
    const defaultUrl = 'https://stremthru.13377001.xyz/';

    if (isEnabled) {
        setElementDisplay('stremthru_url_div', 'block');
        setElementDisplay('stremthru_auth_div', 'block');
        // Set default URL if empty or placeholder
        if (!urlInput.value || urlInput.value === urlInput.placeholder) {
            urlInput.value = defaultUrl;
        }
    } else {
        setElementDisplay('stremthru_url_div', 'none');
        setElementDisplay('stremthru_auth_div', 'none');

        // If StremThru is disabled, disable and uncheck unimplemented services
        unimplementedDebrids.forEach(id => {
            const checkbox = document.getElementById(id);
            if (checkbox && checkbox.checked) {
                checkbox.checked = false;
                // Manually trigger update to hide fields
                updateProviderFields(); 
            }
        });
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
                if (data === null) return; // Skip processing if user hasn't entered PIN yet
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
    const dlEnabled = document.getElementById('debrid_dl').checked || document.getElementById('debrid_dl').disabled;
    const edEnabled = document.getElementById('debrid_ed').checked || document.getElementById('debrid_ed').disabled;
    const ocEnabled = document.getElementById('debrid_oc').checked || document.getElementById('debrid_oc').disabled;
    const pkEnabled = document.getElementById('debrid_pk').checked || document.getElementById('debrid_pk').disabled;
    
    // --- Check StremThru --- 
    const stremthruEnabledCheckbox = document.getElementById('stremthru_enabled');
    const stremthruEnabled = stremthruEnabledCheckbox ? stremthruEnabledCheckbox.checked : false;

    let firstOption = null;

    // --- Add options based on enabled services ---
    if (rdEnabled) {
        firstOption = addDebridToList('Real-Debrid');
    }
    if (adEnabled) {
        // Use ternary for cleaner assignment
        firstOption = firstOption ? firstOption : addDebridToList('AllDebrid'); 
        if (firstOption.value !== 'AllDebrid') addDebridToList('AllDebrid');
    }
    if (tbEnabled) {
        firstOption = firstOption ? firstOption : addDebridToList('TorBox');
        if (firstOption.value !== 'TorBox') addDebridToList('TorBox');
    }
    if (pmEnabled) {
        firstOption = firstOption ? firstOption : addDebridToList('Premiumize');
        if (firstOption.value !== 'Premiumize') addDebridToList('Premiumize');
    }
    if (dlEnabled) {
        firstOption = firstOption ? firstOption : addDebridToList('Debrid-Link');
        if (firstOption.value !== 'Debrid-Link') addDebridToList('Debrid-Link');
    }
    if (edEnabled) {
        firstOption = firstOption ? firstOption : addDebridToList('EasyDebrid');
        if (firstOption.value !== 'EasyDebrid') addDebridToList('EasyDebrid');
    }
    if (ocEnabled) {
        firstOption = firstOption ? firstOption : addDebridToList('Offcloud');
        if (firstOption.value !== 'Offcloud') addDebridToList('Offcloud');
    }
    if (pkEnabled) {
        firstOption = firstOption ? firstOption : addDebridToList('PikPak');
        if (firstOption.value !== 'PikPak') addDebridToList('PikPak');
    }
    
    // --- Add StremThru if enabled --- 
    if (stremthruEnabled) {
        console.log("Adding StremThru as downloader option"); // Debug log
        firstOption = firstOption ? firstOption : addDebridToList('StremThru');
        if (firstOption.value !== 'StremThru') addDebridToList('StremThru');
    }

    // Select the first added option by default if none is selected
    if (firstOption && !document.querySelector('input[name="debrid_downloader"]:checked')) {
        firstOption.checked = true;
    }
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

function toggleDebridOrderList(anyDebridChecked) {
    const debridOrderCheckbox = document.getElementById('debrid_order');
    const debridOrderList = document.getElementById('debridOrderList');
    if (debridOrderCheckbox && debridOrderList) {
        debridOrderCheckbox.disabled = !anyDebridChecked;
        if (!anyDebridChecked) {
            debridOrderCheckbox.checked = false; // Uncheck if no debrid service is enabled
        }
        debridOrderList.classList.toggle('hidden', !(anyDebridChecked && debridOrderCheckbox.checked));
        if (anyDebridChecked && debridOrderCheckbox.checked) {
            updateDebridOrderList(); // Update the list content if visible
        }
    }
}

function toggleDebridDownloaderOptions(anyDebridChecked) {
    const debridDownloaderCheckbox = document.getElementById('debrid_downloader');
    const debridDownloaderOptionsDiv = document.getElementById('debridDownloaderOptions');

    if (debridDownloaderCheckbox && debridDownloaderOptionsDiv) {
        debridDownloaderCheckbox.disabled = !anyDebridChecked;
        if (!anyDebridChecked) {
            debridDownloaderCheckbox.checked = false; // Uncheck if no debrid service is enabled
        }
        debridDownloaderOptionsDiv.classList.toggle('hidden', !(anyDebridChecked && debridDownloaderCheckbox.checked));
    }
}

function updateDebridDownloaderOptions() {
    const debridDownloaderOptions = document.getElementById('debridDownloaderOptions');
    if (!debridDownloaderOptions) return;

    debridDownloaderOptions.innerHTML = '';

    // --- Check standard Debrid services ---
    const rdEnabled = document.getElementById('debrid_rd').checked || document.getElementById('debrid_rd').disabled;
    const adEnabled = document.getElementById('debrid_ad').checked || document.getElementById('debrid_ad').disabled;
    const tbEnabled = document.getElementById('debrid_tb').checked || document.getElementById('debrid_tb').disabled;
    const pmEnabled = document.getElementById('debrid_pm').checked || document.getElementById('debrid_pm').disabled;
    const dlEnabled = document.getElementById('debrid_dl').checked || document.getElementById('debrid_dl').disabled;
    const edEnabled = document.getElementById('debrid_ed').checked || document.getElementById('debrid_ed').disabled;
    const ocEnabled = document.getElementById('debrid_oc').checked || document.getElementById('debrid_oc').disabled;
    const pkEnabled = document.getElementById('debrid_pk').checked || document.getElementById('debrid_pk').disabled;
    
    // --- Check StremThru --- 
    const stremthruEnabledCheckbox = document.getElementById('stremthru_enabled');
    const stremthruEnabled = stremthruEnabledCheckbox ? stremthruEnabledCheckbox.checked : false;

    let firstOption = null;

    // --- Add options based on enabled services ---
    if (rdEnabled) {
        firstOption = addDebridDownloaderOption('Real-Debrid');
    }
    if (adEnabled) {
        // Use ternary for cleaner assignment
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
    
    // --- Add StremThru if enabled --- 
    if (stremthruEnabled) {
        console.log("Adding StremThru as downloader option"); // Debug log
        firstOption = firstOption ? firstOption : addDebridDownloaderOption('StremThru');
        if (firstOption.value !== 'StremThru') addDebridDownloaderOption('StremThru');
    }

    // Select the first added option by default if none is selected
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
    let stremthruWasEnabled = stremthruEnabledCheckbox.checked; // Track initial state
    let stremthruForcedEnable = false;
    let anyUnimplementedChecked = false; // Flag to track if any unimplemented service is checked

    const serviceStates = {};
    const allDebrids = [...implementedDebrids, ...unimplementedDebrids];

    allDebrids.forEach(id => {
        const checkbox = document.getElementById(id);
        if (!checkbox) return;
        const isChecked = checkbox.checked;
        serviceStates[id] = isChecked;

        // Debugging specific service display logic
        if (id === 'debrid_rd') { 
            console.log(`[Debug] ID: ${id}, Checkbox Checked: ${isChecked}`);
        }

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
        if (credDivId) {
             // Debugging setElementDisplay call
            if (id === 'debrid_rd') { 
                console.log(`[Debug] Calling setElementDisplay('${credDivId}', '${isChecked ? 'block' : 'none'}')`);
            }
            setElementDisplay(credDivId, isChecked ? 'block' : 'none');
        }
        

        // Logic for unimplemented debrids forcing StremThru enable
        if (unimplementedDebrids.includes(id) && isChecked) {
            if (!stremthruEnabledCheckbox.checked) {
                stremthruEnabledCheckbox.checked = true;
                stremthruForcedEnable = true; // Mark that we forced it
            }
            anyUnimplementedChecked = true; // Set the flag
        }

        // Show/hide specific credential fields based on checkbox state
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
        if (credDivId) {
            setElementDisplay(credDivId, isChecked ? 'block' : 'none');
        }
    });

    // Manage StremThru checkbox state: disable if any unimplemented service is checked
    if (anyUnimplementedChecked) {
        stremthruEnabledCheckbox.checked = true; // Ensure it's checked
        stremthruEnabledCheckbox.disabled = true; // Disable the checkbox
    } else {
        stremthruEnabledCheckbox.disabled = false; // Re-enable if no unimplemented service is checked
    }

    // If we forced StremThru enable OR its state changed due to disabling, update its fields visibility
    if (stremthruForcedEnable || stremthruEnabledCheckbox.checked !== stremthruWasEnabled || anyUnimplementedChecked) {
        toggleStremThruFields();
    }

    // Check if any debrid service is enabled
    const anyDebridChecked = Object.values(serviceStates).some(state => state); // Recalculate here after all states are set

    // Call the toggle functions AFTER they are defined and AFTER calculating anyDebridChecked
    toggleDebridOrderList(anyDebridChecked); 
    toggleDebridDownloaderOptions(anyDebridChecked);
    ensureDebridConsistency();
    console.log("--- Finished updateProviderFields ---"); // Debug end
}

function ensureDebridConsistency() {
    const RDdebridChecked = document.getElementById('debrid_rd').checked;
    const ADdebridChecked = document.getElementById('debrid_ad').checked;
    const TBdebridChecked = document.getElementById('debrid_tb').checked;
    const PMdebridChecked = document.getElementById('debrid_pm').checked;
    const DLdebridChecked = document.getElementById('debrid_dl').checked;
    const EDdebridChecked = document.getElementById('debrid_ed').checked;
    const OCdebridChecked = document.getElementById('debrid_oc').checked;
    const PKdebridChecked = document.getElementById('debrid_pk').checked;
    const debridOrderChecked = document.getElementById('debrid_order').checked;

    if (!RDdebridChecked && !ADdebridChecked && !TBdebridChecked && !PMdebridChecked && !DLdebridChecked && !EDdebridChecked && !OCdebridChecked && !PKdebridChecked) {
        document.getElementById('debrid_order').checked = false;
        document.getElementById('debridOrderList').classList.add('hidden');
    }

    if (debridOrderChecked && !RDdebridChecked && !ADdebridChecked && !TBdebridChecked && !PMdebridChecked && !DLdebridChecked && !EDdebridChecked && !OCdebridChecked && !PKdebridChecked) {
        document.getElementById('debrid_order').checked = false;
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
        sort: 'qualitythensize',
        exclusion: ['cam', '2160p'],
        languages: ['fr', 'multi'],
        debrid_rd: false,
        debrid_ad: false,
        debrid_tb: false,
        debrid_pm: false,
        debrid_dl: false,
        debrid_ed: false,
        debrid_oc: false,
        debrid_pk: false,
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
    setElementValue('debrid_pm', serviceArray.includes('Premiumize'), defaultConfig.debrid_pm);
    setElementValue('debrid_tb', serviceArray.includes('TorBox'), defaultConfig.debrid_tb);
    setElementValue('debrid_dl', serviceArray.includes('Debrid-Link'), defaultConfig.debrid_dl);
    setElementValue('debrid_ed', serviceArray.includes('EasyDebrid'), defaultConfig.debrid_ed);
    setElementValue('debrid_oc', serviceArray.includes('Offcloud'), defaultConfig.debrid_oc);
    setElementValue('debrid_pk', serviceArray.includes('PikPak'), defaultConfig.debrid_pk);
    setElementValue('debrid_order', serviceArray.length > 0, defaultConfig.debrid_order);
    
    setElementValue('ctg_yggtorrent', decodedData.yggtorrentCtg, defaultConfig.ctg_yggtorrent);
    setElementValue('ctg_yggflix', decodedData.yggflixCtg, defaultConfig.ctg_yggflix);
    
    setElementValue('rd_token_info', decodedData.RDToken, '');
    setElementValue('ad_token_info', decodedData.ADToken, '');
    setElementValue('tb_token_info', decodedData.TBToken, '');
    setElementValue('pm_token_info', decodedData.PMToken, '');
    setElementValue('dl_token_info', decodedData.DLToken, '');
    setElementValue('ed_token_info', decodedData.EDToken, '');
    setElementValue('oc_token_info', decodedData.OCToken, '');
    setElementValue('pk_token_info', decodedData.PKToken, '');
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

function getLink(method) {
    console.error("!!!!!!!!!!!!!! getLink FUNCTION ENTERED !!!!!!!!!!!!!!"); // Very visible log
    console.log("Entering getLink function...");
    // --- Determine Enabled Services and Build Service List ---
    const services = [];
    const data = {
        addonHost: new URL(window.location.href).origin,
        apiKey: document.getElementById('ApiKey').value,
        service: [],
        RDToken: document.getElementById('rd_token_info')?.value,
        ADToken: document.getElementById('ad_token_info')?.value,
        PMToken: document.getElementById('pm_token_info')?.value,
        TBToken: document.getElementById('tb_token_info')?.value,
        DLToken: document.getElementById('dl_token_info')?.value,
        EDToken: document.getElementById('ed_token_info')?.value,
        OCToken: document.getElementById('oc_token_info')?.value,
        PKToken: document.getElementById('pk_token_info')?.value,
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
        stremthru_enabled: document.getElementById('stremthru_enabled')?.checked,
        stremthru_url: document.getElementById('stremthru_url')?.value,
        stremthru_api_key: document.getElementById('stremthru_api_key')?.value
    };

    // Force enable StremThru if it's selected as the downloader
    if (data.debridDownloader === 'StremThru') {
        data.stremthru_enabled = true;
    }

    // --- Determine Enabled Services Directly from Checkboxes ---
    data.service = []; // Start with empty array
    if (document.getElementById('debrid_ad')?.checked) data.service.push('AllDebrid');
    if (document.getElementById('debrid_rd')?.checked) data.service.push('RealDebrid');
    if (document.getElementById('debrid_pm')?.checked) data.service.push('Premiumize');
    if (document.getElementById('debrid_tb')?.checked) data.service.push('Torbox');
    if (document.getElementById('debrid_dl')?.checked) data.service.push('DebridLink');
    if (document.getElementById('debrid_ed')?.checked) data.service.push('EasyNews');
    if (document.getElementById('debrid_oc')?.checked) data.service.push('Offcloud');
    if (document.getElementById('debrid_pk')?.checked) data.service.push('PikPak');

    // Add StremThru if it's enabled (either manually or because it's the downloader)
    if (data.stremthru_enabled || data.debridDownloader === 'StremThru') {
        if (!data.service.includes('StremThru')) { // Avoid duplicates if logic changes elsewhere
             data.service.push('StremThru');
        }
        data.stremthru_enabled = true; // Ensure flag is true if added
    }

    // Set debrid flag if any debrid service OR StremThru is enabled
    data.debrid = data.service.length > 0;

    console.log("Final data object being sent:", JSON.parse(JSON.stringify(data)));

    console.log("Data object JUST BEFORE stringify/encrypt:", JSON.parse(JSON.stringify(data)));

    // Encode data using Base64 (reverted from non-existent encryptData)
    const encodedData = btoa(JSON.stringify(data));

    // Check if mandatory fields are filled
    if (!data.apiKey && !data.cacheUrl) {
        alert("Please fill all required fields: API Key and Cache URL");
        return false;
    }

    // Base URL should point to the manifest endpoint
    const baseUrl = `${window.location.origin}/${encodedData}/manifest.json`;

    // Build the final Stremio link
    const stremioLink = `stremio://${baseUrl.replace(/^https?:\/\//, '')}`;

    if (method === 'link') {
        window.open(stremioLink, "_blank");
    } else if (method === 'copy') {
        // Use the correct baseUrl which already includes /manifest.json
        const link = window.location.protocol + '//' + baseUrl.replace(/^https?:\/\//, '');
        navigator.clipboard.writeText(link).then(() => {
            alert('Link copied to clipboard');
        }, () => {
            alert('Error copying link to clipboard');
        });
    }
}
