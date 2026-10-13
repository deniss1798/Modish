$ErrorActionPreference = 'Stop'
$baonChartTarget = Join-Path $PSScriptRoot 'baon-official-size-chart-evidence-20261010.json'
if (Test-Path -LiteralPath $baonChartTarget) {
    if ((Get-Content -Raw -LiteralPath $baonChartTarget).Trim() -ne 'null') {
        throw 'Evidence target already contains data; preserve it.'
    }
}
$baonGuideResponse = Invoke-WebRequest -Uri 'https://baon.ru/index/sizes/' -UseBasicParsing -TimeoutSec 20
$baonAssetMatch = [regex]::Match($baonGuideResponse.Content, 'src="(?<path>/static/js/main\.[a-f0-9]+\.js)"')
if (-not $baonAssetMatch.Success) { throw 'Official guide script link missing' }
$baonAssetUri = [uri]::new([uri]'https://baon.ru/', $baonAssetMatch.Groups['path'].Value)
if ($baonAssetUri.Host -ne 'baon.ru') { throw 'Official host guard failed' }
$baonPublicScript = Invoke-WebRequest -Uri $baonAssetUri.AbsoluteUri -UseBasicParsing -TimeoutSec 20
if ($baonPublicScript.Content.Length -gt 5242880) { throw 'Public script exceeds text bound' }
$baonBlockStart = $baonPublicScript.Content.IndexOf('gg=[{name:')
if ($baonBlockStart -lt 0) { throw 'Size literal block start missing' }
$baonBlockEnd = $baonPublicScript.Content.IndexOf(',Ig=function', $baonBlockStart)
if ($baonBlockEnd -le $baonBlockStart -or $baonBlockEnd - $baonBlockStart -gt 30000) { throw 'Size literal block bounds not verified' }
$baonLiteralBlock = $baonPublicScript.Content.Substring($baonBlockStart, $baonBlockEnd - $baonBlockStart)
$baonHash = [System.Security.Cryptography.SHA256]::Create()
function Get-BaonTextSha([string]$baonText) {
    ([BitConverter]::ToString($baonHash.ComputeHash([Text.Encoding]::UTF8.GetBytes($baonText)))).Replace('-', '').ToLowerInvariant()
}
$baonScopeById = @{
    gg=@('Женская низ','Джинсы','adult_women_jeans'); xg=@('Мужская низ','Джинсы','adult_men_jeans')
    yg=@('Женская верх','Для новых коллекций','adult_women_upper_new_collections'); bg=@('Женская верх','Size plus','adult_women_upper_size_plus')
    jg=@('Мужская верх','Size plus','adult_men_upper_size_plus'); wg=@('Мужская верх','Для новых коллекций','adult_men_upper_new_collections')
    _g=@('Детская одежда','Для новых коллекций','children_new_collections'); Ng=@('Детская одежда','Для старых коллекций','children_old_collections')
    kg=@('Детская одежда','Носочки','children_socks'); Sg=@('Детская одежда','Головные уборы','children_headwear')
    Eg=@('Детская одежда','Перчатки и варежки','children_gloves'); Cg=@('Мужская верх','Горнолыжные перчатки','adult_men_ski_gloves')
    Og=@('Обувь','Женская и мужская таблица размеров','adult_footwear')
    Tg=@('Женская верх','Калькулятор размеров одежды','adult_women_upper_calculator'); Pg=@('Мужская верх','Калькулятор размеров одежды','adult_men_upper_calculator')
    Mg=@('Мужская низ','Калькулятор размеров джинс','adult_men_jeans_calculator'); Dg=@('Женская низ','Калькулятор размеров джинс','adult_women_jeans_calculator')
}
$baonLiteralMatches = [regex]::Matches($baonLiteralBlock, '(?<id>[A-Za-z_$][A-Za-z0-9_$]*)=\[\{name:')
if ($baonLiteralMatches.Count -ne 17) { throw 'Expected 17 public size data arrays' }
$baonChartArrays = @()
foreach ($baonLiteralMatch in $baonLiteralMatches) {
    $baonArrayId = $baonLiteralMatch.Groups['id'].Value
    if (-not $baonScopeById.ContainsKey($baonArrayId)) { throw 'Unreviewed size array scope' }
    $baonArrayStart = $baonLiteralMatch.Index + $baonArrayId.Length + 1
    $baonArrayEnd = $baonLiteralBlock.IndexOf('}],', $baonArrayStart)
    if ($baonArrayEnd -lt 0) { $baonArrayEnd = $baonLiteralBlock.Length - 2 }
    $baonArrayLiteral = $baonLiteralBlock.Substring($baonArrayStart, $baonArrayEnd + 2 - $baonArrayStart)
    $baonJsonLiteral = [regex]::Replace($baonArrayLiteral, '(?<=\{|,)(name|values|items|key|value):', '"$1":')
    $baonRows = @($baonJsonLiteral | ConvertFrom-Json)
    $baonCleanRows = @()
    $baonRowLengths = @()
    foreach ($baonRow in $baonRows) {
        if ($null -ne $baonRow.values) {
            $baonCleanRows += @{name=$baonRow.name.Trim(); values=@($baonRow.values | ForEach-Object {$_.Trim()})}
            $baonRowLengths += $baonRow.values.Count
        } else {
            $baonCleanRows += @{name=$baonRow.name.Trim(); items=@($baonRow.items | ForEach-Object {@{key=$_.key;value=$_.value.Trim()}})}
            $baonRowLengths += $baonRow.items.Count
        }
    }
    $baonChartArrays += @{
        source_literal_id=$baonArrayId; tab=$baonScopeById[$baonArrayId][0]; heading=$baonScopeById[$baonArrayId][1]; scope=$baonScopeById[$baonArrayId][2]
        role=if($null -ne $baonRows[0].values){'table'}else{'calculator'}
        literal_sha256_utf8=Get-BaonTextSha $baonArrayLiteral
        raw_rows=$baonRows; clean_rows=$baonCleanRows; row_lengths=$baonRowLengths
        row_lengths_equal=(@($baonRowLengths | Select-Object -Unique).Count -eq 1)
    }
}
$baonEvidence = [ordered]@{
    schema='official_size_chart_evidence_v1'; checked_date_moscow='2026-10-10'; fetched_at_utc=[DateTime]::UtcNow.ToString('o')
    primary_page_url='https://baon.ru/index/sizes/'; public_asset_url=$baonAssetUri.AbsoluteUri
    page_http_status=[int]$baonGuideResponse.StatusCode; asset_http_status=[int]$baonPublicScript.StatusCode
    page_content_sha256_utf8=Get-BaonTextSha $baonGuideResponse.Content
    asset_body_sha256=([BitConverter]::ToString($baonHash.ComputeHash($baonPublicScript.RawContentStream.ToArray()))).Replace('-', '').ToLowerInvariant()
    asset_decoded_content_sha256_utf8=Get-BaonTextSha $baonPublicScript.Content
    literal_block_sha256_utf8=Get-BaonTextSha $baonLiteralBlock
    extraction=@{
        method='Parse 17 finite name/values or name/items literals from the script linked by official public HTML; quote five object-key aliases then use ConvertFrom-Json; never execute site JavaScript'
        bounds=@{max_script_text_chars=5242880;max_literal_block_chars=30000;request_timeout_seconds=20}
        cleaning='Trim label/value whitespace only; preserve comma decimals, ranges and source terminology; retain raw rows'
        scope_verification='Public script TSTable rendering associations and calculator references; women upper, women lower, men upper, men lower, footwear and child tab enum'
        browser_observation_date='2026-10-09'; full_current_browser_recheck=$false
        note='No browser was available on 2026-10-10; current public static-source retrieval corroborates previous UI rows. Unequal row lengths are preserved, not repaired.'
    }
    arrays=$baonChartArrays; accepted_feed_mapping=$false; same_variant_alias_relation_proven=$false; same_variant_stock_proven=$false
    eligibility_policy='research_only_no_strict_matching_promotion'
}
$baonEvidence | ConvertTo-Json -Depth 15 | Set-Content -LiteralPath $baonChartTarget -Encoding utf8NoBOM
$baonHash.Dispose()
Get-FileHash -LiteralPath $baonChartTarget -Algorithm SHA256 | Format-List Path,Hash
$baonChartArrays | Where-Object {-not $_.row_lengths_equal} | Select-Object source_literal_id,row_lengths
$baonEvidence | Select-Object public_asset_url,asset_body_sha256,fetched_at_utc | Format-List
