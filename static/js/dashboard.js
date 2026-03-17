function openTab(name) {
    // Cacher tous les onglets
    document.getElementById("forex").style.display = "none";
    document.getElementById("portfolio").style.display = "none";

    // Afficher l'onglet sélectionné
    document.getElementById(name).style.display = "block";
}

function loadForex(pair) {
    fetch("/forex?pair=" + pair)
        .then(r => r.text())
        .then(html => {
            document.getElementById("forex_content").innerHTML = html;
        })
        .catch(error => {
            console.error('Erreur lors du chargement des données forex:', error);
            document.getElementById("forex_content").innerHTML = "<p>Erreur lors du chargement des données forex</p>";
        });
}

function updateCalgaryTime() {
    fetch("/time")
        .then(r => r.json())
        .then(data => {
            document.getElementById("calgary-time").innerText = data.calgary_time;
        })
        .catch(error => console.error('Erreur temps Calgary:', error));
}

// Charger les données forex par défaut
document.addEventListener('DOMContentLoaded', function() {
    loadForex("EURUSD=X");
    
    // Refresh toutes les 5 minutes
    setInterval(function() {
        updateCalgaryTime();
        // Recharger les données forex actives
        const activeSelect = document.querySelector('#forex select');
        if (activeSelect) {
            loadForex(activeSelect.value);
        }
    }, 5 * 60 * 1000); // 5 minutes
});