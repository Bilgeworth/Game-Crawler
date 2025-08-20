# direct copy from your current strings to keep look/feel identical
INDEX_HTML =  r"""<!doctype html>
<html lang="en" data-bs-theme="dark">
<head>
  <meta charset="utf-8">
  <title>{{ app_title }}</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet">
  <style>
    .card { border: 1px solid rgba(255,255,255,.08); }
    .game-cover { width: 100%; height: 260px; object-fit: cover; border-radius: .5rem .5rem 0 0; background:#222; }
    .title { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
    .path { color: rgba(255,255,255,.6); }
    .unsupported .game-cover { filter: grayscale(1) brightness(.7); }
  </style>
</head>
<body>
<nav class="navbar navbar-expand-lg bg-body-tertiary px-3">
  <a class="navbar-brand" href="#">{{ app_title }}</a>
  <div class="ms-auto d-flex gap-2">
    <a class="btn btn-outline-light btn-sm" href="{{ url_for('gamecrawler.settings') }}">Settings</a>
    <a class="btn btn-outline-light btn-sm" href="{{ url_for('gamecrawler.rescan') }}">Rescan</a>
  </div>
</nav>

<div class="container py-4">
  {% with messages = get_flashed_messages() %}
    {% if messages %}
      <div class="alert alert-warning">{{ messages|join('. ') }}</div>
    {% endif %}
  {% endwith %}

  <div class="mb-3 small">
    Global default: <strong>{{ 'Sandboxed' if global_default else 'Not sandboxed' }}</strong>
    {% if not sandboxie_available %}
      <span class="text-warning ms-2">[Sandboxie not found]</span>
    {% endif %}
  </div>

  {% if not games %}
    <div class="text-center py-5">
      <h4>No games found in <code>{{ root }}</code>.</h4>
      <p class="text-secondary">Add one folder per game. Put a cover image and your .exe / .sh in the folder (subfolders OK).</p>
    </div>
  {% else %}
  <div class="row row-cols-1 row-cols-sm-2 row-cols-md-3 row-cols-xl-5 g-4">
    {% for g in games %}
      {% set has_exec = (g.detected_execs|length) > 0 %}
      {% set eff_sb = (g.meta.sandboxed if g.meta.sandboxed is not none else global_default) %}
      <div class="col">
        <div class="card h-100 shadow-sm {% if not has_exec %}unsupported{% endif %}">
          <img class="game-cover" src="{{ url_for('gamecrawler.cover', game_id=g.id) }}" alt="cover">
          <div class="card-body d-flex flex-column">
            <div class="title fw-semibold" title="{{ g.meta.title }}">
              {{ g.meta.title }}
              {% if running_ids and g.id in running_ids %}
                <span class="badge text-bg-warning ms-2">Running</span>
              {% endif %}
            </div>
            <div class="small path mt-1">{{ g.rel }}</div>

            <div class="mt-2 d-flex flex-wrap gap-2">
              {% if not has_exec and (g.meta.launchers|length) == 0 %}
                <a class="btn btn-outline-light btn-sm" href="{{ url_for('gamecrawler.edit_game', game_id=g.id) }}">Edit</a>
                <span class="badge text-bg-secondary">No executable</span>
              {% else %}
                <a class="btn btn-success btn-sm" href="{{ url_for('gamecrawler.launch_select', game_id=g.id) }}">Run</a>
                <a class="btn btn-outline-warning btn-sm {% if not sandboxie_available %}disabled{% endif %}"
                   {% if sandboxie_available %}href="{{ url_for('gamecrawler.launch_select', game_id=g.id) }}?sandbox=1"{% endif %}
                   {% if not sandboxie_available %}tabindex="-1" aria-disabled="true"{% endif %}>
                   Run Sandboxed
                </a>
                <a class="btn btn-outline-light btn-sm" href="{{ url_for('gamecrawler.edit_game', game_id=g.id) }}">Edit</a>
              {% endif %}
              {% if eff_sb %}
                <span class="badge text-bg-warning align-self-center">
                  {{ 'Sandboxed' if g.meta.sandboxed is not none else 'Sandboxed (global)' }}
                </span>
              {% endif %}
            </div>
          </div>
        </div>
      </div>
    {% endfor %}
  </div>
  {% endif %}
</div>
</body>
</html>
"""
EDIT_HTML  = r"""<!doctype html>
<html lang="en" data-bs-theme="dark">
<head>
  <meta charset="utf-8">
  <title>Edit {{ game.meta.title }} — {{ app_title }}</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet">
  <style>
    .card { border: 1px solid rgba(255,255,255,.08); }
    .cover-preview { width: 260px; height: 260px; object-fit: cover; border-radius: .5rem; background:#222; }
    .path { color: rgba(255,255,255,.6); }
    code.path { word-break: break-all; }
  </style>
</head>
<body>
<nav class="navbar navbar-expand-lg bg-body-tertiary px-3">
  <a class="navbar-brand" href="{{ url_for('gamecrawler.index') }}">{{ app_title }}</a>
  <div class="ms-auto">
    <a class="btn btn-outline-light btn-sm" href="{{ url_for('gamecrawler.settings') }}">Settings</a>
  </div>
</nav>

<div class="container py-4">
  {% with messages = get_flashed_messages() %}
    {% if messages %}
      <div class="alert alert-warning">{{ messages|join('. ') }}</div>
    {% endif %}
  {% endwith %}

  <div class="row g-4">
    <div class="col-md-4">
      <img id="coverPreview" class="cover-preview" src="{{ url_for('gamecrawler.cover', game_id=game.id) }}" alt="cover">
      <div class="small path mt-2">{{ game.rel }}</div>
    </div>
    <div class="col-md-8">
      <div class="card p-3">
        <form id="editForm" action="{{ url_for('gamecrawler.edit_game', game_id=game.id) }}" method="post" enctype="multipart/form-data">
          <div class="mb-3">
            <label class="form-label">Title</label>
            <input class="form-control" type="text" name="title" value="{{ game.meta.title }}" required>
          </div>

          <div class="mb-3">
            <label class="form-label">Sandboxing</label>
            <div class="form-check">
              <input class="form-check-input" type="radio" name="sandbox_choice" id="sb_global" value="global" {% if game.meta.sandboxed is none %}checked{% endif %}>
              <label class="form-check-label" for="sb_global">Use global default</label>
            </div>
            <div class="form-check">
              <input class="form-check-input" type="radio" name="sandbox_choice" id="sb_on" value="on" {% if game.meta.sandboxed is true %}checked{% endif %}>
              <label class="form-check-label" for="sb_on">Always sandbox this game</label>
            </div>
            <div class="form-check">
              <input class="form-check-input" type="radio" name="sandbox_choice" id="sb_off" value="off" {% if game.meta.sandboxed is false %}checked{% endif %}>
              <label class="form-check-label" for="sb_off">Never sandbox this game</label>
            </div>
          </div>

          <hr class="my-3">

          <h6>Configured launch options</h6>
          {% if game.meta.launchers %}
            <div class="table-responsive mb-3">
              <table class="table table-dark table-sm align-middle">
                <thead><tr><th style="width:22%">Name</th><th>Relative path</th><th style="width:26%">Args</th><th style="width:10%"></th></tr></thead>
                <tbody>
                {% for L in game.meta.launchers %}
                  <tr>
                    <td><input class="form-control form-control-sm" type="text" name="name_{{ L.id }}" value="{{ L.name }}" required></td>
                    <td><code class="path">{{ L.relpath }}</code></td>
                    <td><input class="form-control form-control-sm" type="text" name="args_{{ L.id }}" value="{{ L.args }}"></td>
                    <td>
                      <button class="btn btn-outline-danger btn-sm" name="remove_id" value="{{ L.id }}" type="submit">Remove</button>
                    </td>
                  </tr>
                {% endfor %}
                </tbody>
              </table>
            </div>
          {% else %}
            <div class="alert alert-info">No launch options configured yet. Add from detected executables below.</div>
          {% endif %}

          <div class="mb-2">
            <button class="btn btn-primary" type="submit" name="action" value="save_all">Save</button>
            <a class="btn btn-secondary" href="{{ url_for('gamecrawler.index') }}">Back</a>
            {% if game.meta.launchers|length > 0 %}
              <a class="btn btn-success" href="{{ url_for('gamecrawler.launch_select', game_id=game.id) }}">Run…</a>
            {% endif %}
          </div>

          <hr class="my-3">

          <h6>Detected executables (subfolders included)</h6>
          {% if game.detected_execs %}
            <div class="list-group">
              {% for p in game.detected_execs %}
                <div class="list-group-item d-flex justify-content-between align-items-center">
                  <code class="path">{{ p }}</code>
                  <button class="btn btn-outline-info btn-sm" name="add_exec" value="{{ p }}">Add</button>
                </div>
              {% endfor %}
            </div>
          {% else %}
            <div class="alert alert-secondary">No executables found. Supported: {{ ALLOWED_EXEC_EXT|join(', ') }}</div>
          {% endif %}

          <hr class="my-3">

          <div class="mb-3">
            <label class="form-label">Cover image</label>
            <div class="row g-2">
              {% for img in game.detected_images %}
              <div class="col-6 col-md-4">
                <div class="form-check">
                  <input class="form-check-input cover-radio" type="radio" name="cover_choice" id="img_{{ loop.index }}" value="{{ img }}" {% if img == game.meta.cover_image %}checked{% endif %}>
                  <label class="form-check-label" for="img_{{ loop.index }}">{{ img }}</label>
                </div>
              </div>
              {% endfor %}
            </div>
            <div class="form-text">Or upload (png/jpg/webp). It will be saved in this game’s folder.</div>
            <input id="coverUpload" class="form-control mt-2" type="file" name="cover_upload" accept=".png,.jpg,.jpeg,.webp">
          </div>
        </form>
      </div>
    </div>
  </div>
</div>

<script>
  // Live cover preview: radio selection -> exact image
  document.querySelectorAll('.cover-radio').forEach(r => {
    r.addEventListener('change', () => {
      const imgName = r.value;
      const preview = document.getElementById('coverPreview');
      preview.src = "{{ url_for('gamecrawler.game_file', game_id=game.id, filename='__REPLACE__') }}"
        .replace('__REPLACE__', encodeURIComponent(imgName))
        + "?_cb=" + Date.now();
    });
  });

  // Live cover preview: file upload
  const up = document.getElementById('coverUpload');
  if (up) {
    up.addEventListener('change', () => {
      const file = up.files && up.files[0];
      if (!file) return;
      document.getElementById('coverPreview').src = URL.createObjectURL(file);
    });
  }
</script>

</body>
</html>
"""
LAUNCH_HTML = r"""<!doctype html>
<html lang="en" data-bs-theme="dark">
<head>
  <meta charset="utf-8">
  <title>Launch {{ game.meta.title }} — {{ app_title }}</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet">
</head>
<body>
<nav class="navbar navbar-expand-lg bg-body-tertiary px-3">
  <a class="navbar-brand" href="{{ url_for('gamecrawler.index') }}">{{ app_title }}</a>
</nav>

<div class="container py-4">
  <h5 class="mb-3">{{ game.meta.title }}</h5>
  {% if not game.meta.launchers %}
    <div class="alert alert-info">No launch options configured. <a href="{{ url_for('gamecrawler.edit_game', game_id=game.id) }}">Add some</a>.</div>
  {% else %}
    <form class="card p-3" action="{{ url_for('gamecrawler.launch_execute', game_id=game.id) }}" method="post">
      <div class="mb-3">
        {% for L in game.meta.launchers %}
          <div class="form-check">
            <input class="form-check-input" type="radio" name="launcher_id" id="L{{ L.id }}" value="{{ L.id }}"
              {% if (game.meta.last_launcher and game.meta.last_launcher == L.id) or (not game.meta.last_launcher and loop.first) %}checked{% endif %}>
            <label class="form-check-label" for="L{{ L.id }}">
              <strong>{{ L.name }}</strong> <span class="text-secondary"> — {{ L.relpath }}{% if L.args %} {{ L.args }}{% endif %}</span>
            </label>
          </div>
        {% endfor %}
      </div>
      <div class="d-flex gap-2">
        <button class="btn btn-success" type="submit" name="mode" value="normal">Launch</button>
        <button class="btn btn-outline-warning" type="submit" name="mode" value="sandboxed" {% if not sandboxie_available %}disabled{% endif %}>Launch Sandboxed</button>
        <a class="btn btn-secondary ms-auto" href="{{ url_for('gamecrawler.index') }}">Cancel</a>
      </div>
    </form>
  {% endif %}
</div>
</body>
</html>
"""
SETTINGS_HTML = r"""<!doctype html>
<html lang="en" data-bs-theme="dark">
<head>
  <meta charset="utf-8">
  <title>Settings — {{ app_title }}</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet">
</head>
<body>
<nav class="navbar navbar-expand-lg bg-body-tertiary px-3">
  <a class="navbar-brand" href="{{ url_for('gamecrawler.index') }}">{{ app_title }}</a>
</nav>

<div class="container py-4">
  {% with messages = get_flashed_messages() %}
    {% if messages %}
      <div class="alert alert-warning">{{ messages|join('. ') }}</div>
    {% endif %}
  {% endwith %}

  <form action="{{ url_for('gamecrawler.settings') }}" method="post" class="card p-3">
    <h5 class="mb-3">Global Settings</h5>

    <div class="form-check form-switch mb-3">
      <input class="form-check-input" type="checkbox" role="switch" id="defSandbox" name="default_sandboxed" {% if global_default %}checked{% endif %}>
      <label class="form-check-label" for="defSandbox">Default to sandboxed</label>
      <div class="form-text">Applies to games set to “Use Global”. Per-game overrides take precedence.</div>
    </div>

    <div class="mb-3">
      <label class="form-label">Sandboxie Start.exe</label>
      {% if sbie_path %}
        <div class="alert alert-success py-2 mb-2">Found at: <code>{{ sbie_path }}</code></div>
      {% else %}
        <div class="alert alert-warning py-2 mb-2">
          Sandboxie Plus not found. Install to <code>C:\Program Files\Sandboxie-Plus\</code>
          or set the <code>SANDBOXIE_START</code> environment variable to your custom <code>Start.exe</code> location.
        </div>
      {% endif %}
    </div>

    <p class="mt-2">
      PowerShell WSL wrapper: 
      {% if ps_wrapper %}<code>{{ ps_wrapper }}</code>{% else %}<em>Not set</em>{% endif %}
    </p>

    <h3 class="mt-4">Ignore Patterns ({{ ignore_file }})</h3>
    <p class="text-muted">
      One pattern per line. Uses basic <code>fnmatch</code> globbing. Examples:
    </p>
    <ul>
      <li><code>.git/</code> – ignore a folder</li>
      <li><code>*/temp/*</code> – ignore any "temp" subfolders</li>
      <li><code>!keepme/</code> – re-include a path previously ignored</li>
    </ul>

    <textarea name="ignore_text" rows="10" class="form-control" style="font-family:monospace;">{{ ignore_text }}</textarea>

    <div class="d-flex gap-2">
      <button class="btn btn-primary" type="submit">Save</button>
      <a class="btn btn-secondary" href="{{ url_for('gamecrawler.index') }}">Back</a>
    </div>
  </form>
</div>
</body>
</html>
"""