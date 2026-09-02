<template>
  <div class="h-screen flex flex-col overflow-hidden bg-base-200" :class="{ 'select-none cursor-col-resize': isResizing || isResizingAnalysis }">

    <!-- ── Header ── -->
    <header class="flex items-center gap-2 px-3 h-11 bg-base-100 border-b border-base-300 flex-shrink-0 z-50">

      <!-- Logo + title (click to view collections) -->
      <button
        class="btn btn-ghost btn-xs gap-1.5 normal-case h-7 min-h-0 px-1.5"
        :class="{ 'bg-base-200': activeTab === 'collections' }"
        @click="activeTab = 'collections'"
        title="View all collections"
        aria-label="Asymptote — view all collections"
      >
        <img src="/icon_black.svg" alt="" class="logo-header h-5 w-5 flex-shrink-0">
        <span class="font-bold text-sm tracking-tight hidden sm:inline">Asymptote</span>
      </button>

      <!-- Current collection indicator -->
      <button
        class="btn btn-xs btn-ghost gap-1.5 normal-case font-normal h-7 min-h-0"
        @click="activeTab = 'collections'"
        title="View all collections"
        :aria-label="`Current collection: ${collectionStore.currentCollection?.name || 'Default'}. Click to view all collections.`"
      >
        <div
          class="w-2 h-2 rounded-full flex-shrink-0"
          :style="{ backgroundColor: collectionStore.currentCollection?.color || '#3b82f6' }"
        ></div>
        <span class="max-w-32 truncate text-xs">{{ collectionStore.currentCollection?.name || 'Default' }}</span>
      </button>

      <div class="w-px h-5 bg-base-300 mx-0.5 flex-shrink-0"></div>

      <!-- Sources toggle (hidden on collections overview) -->
      <button
        v-if="!isCollectionsView"
        class="btn btn-xs btn-ghost gap-1"
        :class="sourcesSidebarOpen ? 'btn-active' : ''"
        @click="sourcesSidebarOpen = !sourcesSidebarOpen"
        title="Toggle sources panel"
        aria-label="Toggle sources panel"
        :aria-pressed="sourcesSidebarOpen"
      >
        <Library :size="14" />
        <span class="hidden md:inline text-xs">Sources</span>
      </button>

      <!-- Tabs (hidden on collections overview) -->
      <template v-if="!isCollectionsView">
        <button
          v-for="tab in tabs"
          :key="tab.id"
          class="btn btn-xs btn-ghost gap-1.5 rounded-md transition-all"
          :class="activeTab === tab.id ? 'bg-base-200 font-semibold' : 'font-normal'"
          @click="activeTab = tab.id"
          :aria-label="tab.label"
          :aria-current="activeTab === tab.id ? 'page' : undefined"
        >
          <component :is="tab.icon" :size="14" />
          <span class="hidden sm:inline">{{ tab.label }}</span>
        </button>
      </template>

      <!-- Spacer -->
      <div class="flex-1"></div>

      <!-- User identity (private-collections mode) -->
      <div v-if="userStore.isPrivateMode" class="hidden md:flex items-center gap-1.5 text-xs text-base-content/50">
        <Users :size="12" />
        <span class="max-w-24 truncate">{{ userStore.displayName }}</span>
      </div>

      <!-- Analysis sidebar toggle (hidden on collections overview) -->
      <button
        v-if="!isCollectionsView"
        class="btn btn-ghost btn-circle btn-sm"
        :class="{ 'bg-base-300': analysisSidebarOpen }"
        @click="analysisSidebarOpen = !analysisSidebarOpen"
        title="Toggle Studio panel"
        aria-label="Toggle Studio panel"
        :aria-pressed="analysisSidebarOpen"
      >
        <PanelRightOpen :size="16" :class="analysisSidebarOpen ? 'rotate-180 transition-transform' : 'transition-transform'" />
      </button>

      <!-- Global AI provider pill: app-wide default + click-to-switch menu -->
      <div v-if="providerPill.configured.length > 0" class="dropdown dropdown-end hidden sm:block">
        <label
          tabindex="0"
          class="btn btn-xs btn-ghost gap-1 normal-case font-normal h-7 min-h-0 border border-base-300 rounded-full px-2.5"
          :title="`Default AI provider: ${providerPill.name}`"
          :aria-label="`Default AI provider: ${providerPill.name}${providerPill.model ? ', model ' + providerPill.model : ''}. Click to switch.`"
          aria-haspopup="menu"
        >
          <Sparkles :size="11" class="text-primary flex-shrink-0" aria-hidden="true" />
          <span class="text-xs max-w-44 truncate">
            {{ providerPill.name }}<span v-if="providerPill.model" class="text-base-content/50"> · {{ providerPill.model }}</span>
          </span>
          <ChevronDown :size="10" class="text-base-content/40 flex-shrink-0" aria-hidden="true" />
        </label>
        <ul tabindex="0" class="dropdown-content z-[60] menu p-2 shadow-lg bg-base-100 border border-base-300 rounded-box w-64">
          <li class="menu-title"><span class="text-xs">Default AI provider</span></li>
          <li v-for="pid in providerPill.configured" :key="pid">
            <button
              class="flex items-center gap-2"
              :class="{ 'active': pid === providerPill.id }"
              @click="setGlobalProvider(pid)"
            >
              <Check v-if="pid === providerPill.id" :size="12" class="flex-shrink-0" aria-hidden="true" />
              <span v-else class="w-3 flex-shrink-0" aria-hidden="true"></span>
              <span class="flex-1 text-left text-sm truncate">{{ providerDisplayName(pid) }}</span>
              <span v-if="providerPill.teamIds.includes(pid)" class="badge badge-success badge-xs">Team key</span>
            </button>
          </li>
          <li class="menu-title pt-1"><span class="text-xs font-normal text-base-content/50">Chat can override this per conversation.</span></li>
        </ul>
      </div>

      <!-- Help menu -->
      <div class="dropdown dropdown-end">
        <button tabindex="0" class="btn btn-ghost btn-circle btn-sm" title="Help" aria-label="Help menu" aria-haspopup="menu">
          <HelpCircle :size="16" />
        </button>
        <ul tabindex="0" class="dropdown-content menu bg-base-100 rounded-box z-50 w-56 p-2 shadow border border-base-300">
          <li>
            <a href="https://github.com/jordanboyce/asymptote#readme" target="_blank" rel="noopener">
              <BookOpen :size="14" />
              Documentation
            </a>
          </li>
          <li>
            <button @click="showOnboarding = true">
              <Sparkles :size="14" />
              Run setup again
            </button>
          </li>
        </ul>
      </div>

      <!-- Settings button -->
      <button
        class="btn btn-ghost btn-circle btn-sm"
        :class="{ 'bg-base-300': activeTab === 'settings' }"
        @click="activeTab = 'settings'"
        title="Settings"
        aria-label="Settings"
        :aria-current="activeTab === 'settings' ? 'page' : undefined"
      >
        <Settings :size="16" />
      </button>

      <!-- Signed-in identity + sign out (Cloudflare Access deployments only) -->
      <div v-if="me.authenticated_via === 'cloudflare-access'" class="dropdown dropdown-end">
        <button tabindex="0" class="btn btn-ghost btn-circle btn-sm" :title="me.identity || 'Signed in'" aria-label="Account menu">
          <CircleUser :size="16" />
        </button>
        <ul tabindex="0" class="dropdown-content menu bg-base-100 rounded-box z-50 w-64 p-2 shadow border border-base-300">
          <li class="menu-title"><span class="truncate">{{ me.identity || 'Signed in' }}</span></li>
          <li>
            <a :href="me.logout_url" class="text-error">
              <LogOut :size="14" />
              Sign out
            </a>
          </li>
        </ul>
      </div>
    </header>

    <!-- ── Body ── -->
    <div class="flex flex-1 overflow-hidden min-h-0 relative">

      <!-- Compact mode: side panels overlay the main surface instead of
           squeezing it, so the answer column keeps the full window width.
           This backdrop closes whichever panel is open. -->
      <div
        v-if="isCompact && (sourcesSidebarOpen || analysisSidebarOpen)"
        class="absolute inset-0 z-30 bg-base-content/20"
        @click="sourcesSidebarOpen = false; analysisSidebarOpen = false"
        aria-hidden="true"
      ></div>

      <!-- Left sidebar: sources (resizable, hidden on collections overview) -->
      <aside
        v-show="!isCollectionsView"
        class="bg-base-100 overflow-hidden flex flex-col"
        :class="[
          isResizing ? '' : 'transition-all duration-200 ease-in-out',
          isCompact ? 'absolute left-0 top-0 h-full z-40 shadow-2xl' : 'flex-shrink-0 relative',
        ]"
        :style="{ width: sourcesSidebarOpen ? effectiveSidebarWidth + 'px' : '0px' }"
      >
        <!-- Sidebar content fixed to sidebarWidth so it doesn't shrink during close animation -->
        <div class="h-full flex flex-col" :style="{ width: effectiveSidebarWidth + 'px' }">
          <SourcesSidebar
            @document-deleted="handleDocumentDeleted"
            @background-job-started="showJobsDrawer = true"
            @close="sourcesSidebarOpen = false"
          />
        </div>

        <!-- Drag handle (pointless in compact overlay mode) -->
        <div
          v-if="sourcesSidebarOpen && !isCompact"
          class="absolute right-0 top-0 h-full w-1 cursor-col-resize group z-10 hover:bg-primary/40 transition-colors"
          :class="isResizing ? 'bg-primary/60' : ''"
          @mousedown.prevent="startResize"
        >
          <!-- Visual grip dots -->
          <div class="absolute inset-y-0 right-0 flex items-center justify-center w-1">
            <div class="flex flex-col gap-1 opacity-0 group-hover:opacity-60 transition-opacity">
              <div class="w-1 h-1 rounded-full bg-base-content"></div>
              <div class="w-1 h-1 rounded-full bg-base-content"></div>
              <div class="w-1 h-1 rounded-full bg-base-content"></div>
            </div>
          </div>
        </div>
      </aside>

      <!-- Main panel -->
      <main class="flex-1 flex flex-col overflow-hidden min-w-0">

        <!-- Tab content -->
        <div
          class="flex-1 min-h-0"
          :class="activeTab === 'chat' ? 'overflow-hidden p-0' : 'overflow-y-auto'"
        >
          <!-- Chat gets full height, no padding wrapper -->
          <div v-if="activeTab === 'chat'" class="h-full p-4">
            <ChatTab @switch-tab="switchTab" />
          </div>

          <!-- All other tabs: padded scroll container -->
          <div v-else class="px-6 py-6">

            <!-- Collections overview -->
            <div v-if="activeTab === 'collections'" class="pt-4">
              <header class="flex items-end justify-between gap-6 flex-wrap pb-5 mb-4 border-b border-base-300/60">
                <div class="min-w-0">
                  <h1 class="text-[22px] leading-none font-semibold tracking-tight">Collections</h1>
                  <p class="mt-2 text-xs text-base-content/50">
                    <span class="tabular-nums font-medium text-base-content/70">{{ collectionStore.sortedCollections.length }}</span>
                    {{ collectionStore.sortedCollections.length === 1 ? 'collection' : 'collections' }}
                    <span class="mx-1.5 text-base-content/25">·</span>
                    <span class="text-base-content/45">workspace for your sources</span>
                  </p>
                </div>

                <div class="flex items-center gap-2 flex-wrap">
                  <!-- Search -->
                  <div class="relative">
                    <Search :size="13" class="absolute left-2.5 top-1/2 -translate-y-1/2 text-base-content/40 pointer-events-none" />
                    <input
                      v-model="collectionsSearch"
                      type="text"
                      placeholder="Search collections"
                      class="input input-sm input-bordered pl-7 pr-7 w-56 focus:w-64 transition-[width]"
                      aria-label="Search collections"
                    />
                    <button
                      v-if="collectionsSearch"
                      @click="collectionsSearch = ''"
                      class="absolute right-2 top-1/2 -translate-y-1/2 text-base-content/40 hover:text-base-content transition-colors"
                      aria-label="Clear search"
                    >
                      <X :size="13" />
                    </button>
                  </div>

                  <!-- View toggle -->
                  <div class="join" role="group" aria-label="View mode">
                    <button
                      class="btn btn-sm join-item"
                      :class="collectionsView === 'list' ? 'btn-active' : 'btn-ghost'"
                      @click="collectionsView = 'list'"
                      title="List view"
                      aria-label="List view"
                      :aria-pressed="collectionsView === 'list'"
                    >
                      <List :size="14" />
                    </button>
                    <button
                      class="btn btn-sm join-item"
                      :class="collectionsView === 'cards' ? 'btn-active' : 'btn-ghost'"
                      @click="collectionsView = 'cards'"
                      title="Card view"
                      aria-label="Card view"
                      :aria-pressed="collectionsView === 'cards'"
                    >
                      <LayoutGrid :size="14" />
                    </button>
                  </div>

                  <!-- Sort -->
                  <div class="dropdown dropdown-end">
                    <label
                      tabindex="0"
                      class="btn btn-sm btn-ghost gap-1.5 normal-case font-normal"
                      aria-label="Sort collections"
                    >
                      {{ collectionsSortLabel }}
                      <ChevronDown :size="12" />
                    </label>
                    <ul tabindex="0" class="dropdown-content z-[50] menu p-1 shadow-lg bg-base-100 border border-base-300 rounded-box w-44">
                      <li v-for="opt in collectionsSortOptions" :key="opt.id">
                        <a
                          @click="collectionsSort = opt.id"
                          :class="{ 'font-semibold bg-base-200': collectionsSort === opt.id }"
                          class="text-sm"
                        >{{ opt.label }}</a>
                      </li>
                    </ul>
                  </div>

                  <div class="w-px h-5 bg-base-300/70 mx-0.5"></div>

                  <!-- CTA -->
                  <button
                    class="btn btn-sm btn-ghost gap-1.5 normal-case font-medium border border-base-300 hover:border-base-content/30"
                    @click="openCreateCollectionModal"
                  >
                    <Plus :size="14" />
                    New collection
                  </button>
                  <button
                    v-if="userStore.privateCollections"
                    class="btn btn-sm btn-ghost gap-1.5 normal-case font-medium border border-base-300 hover:border-base-content/30"
                    @click="openJoinSharedModal"
                    title="Accept a share token someone sent you"
                  >
                    <Share2 :size="14" />
                    Join shared
                  </button>
                </div>
              </header>

              <!-- Empty search result -->
              <div
                v-if="filteredCollections.length === 0 && collectionsSearch"
                class="text-center py-16 text-sm text-base-content/45"
              >
                No collections match
                <span class="text-base-content/70">"{{ collectionsSearch }}"</span>
              </div>

              <!-- List view -->
              <ul
                v-else-if="collectionsView === 'list'"
                class="divide-y divide-base-300/50"
              >
                <template v-for="(collection, index) in filteredCollections" :key="collection.id">
                <li
                  v-if="index === firstSharedIndex"
                  class="pt-6 pb-1 !border-t-0 list-none"
                  aria-hidden="true"
                >
                  <span class="text-[10px] uppercase tracking-[0.14em] font-semibold text-base-content/40">Shared with me</span>
                </li>
                <li
                  class="group flex items-start gap-5 py-5 cursor-pointer transition-colors focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary rounded-sm"
                  @click="selectCollectionAndNavigate(collection.id)"
                  :aria-current="collection.id === collectionStore.currentCollectionId ? 'true' : undefined"
                  role="button"
                  tabindex="0"
                  @keydown.enter="selectCollectionAndNavigate(collection.id)"
                  @keydown.space.prevent="selectCollectionAndNavigate(collection.id)"
                >
                  <span
                    class="mt-[0.55rem] w-2 h-2 rounded-full flex-shrink-0 ring-4 ring-transparent transition-all"
                    :class="{ 'ring-base-200': collection.id === collectionStore.currentCollectionId }"
                    :style="{ backgroundColor: collection.color }"
                    aria-hidden="true"
                  ></span>

                  <div class="flex-1 min-w-0">
                    <div class="flex items-baseline flex-wrap gap-x-3 gap-y-1">
                      <h3
                        class="text-[15px] truncate group-hover:text-primary transition-colors"
                        :class="collection.id === collectionStore.currentCollectionId ? 'font-semibold' : 'font-medium'"
                      >{{ collection.name }}</h3>
                      <span
                        v-if="collection.id === collectionStore.currentCollectionId"
                        class="text-[10px] uppercase tracking-[0.14em] font-semibold text-base-content/55"
                      >Active</span>
                      <span
                        v-if="collection.shared"
                        class="text-[10px] uppercase tracking-[0.14em] text-base-content/40"
                      >Shared</span>
                      <span
                        v-else-if="userStore.privateCollections && collection.team"
                        class="text-[10px] uppercase tracking-[0.14em] text-base-content/40"
                      >Team</span>
                      <span
                        v-else-if="userStore.privateCollections"
                        class="text-[10px] uppercase tracking-[0.14em] text-base-content/40"
                      >Private</span>
                    </div>
                    <p v-if="collection.description" class="mt-1.5 text-[13px] leading-relaxed text-base-content/55 truncate">{{ collection.description }}</p>
                  </div>

                  <div class="flex items-center gap-4 flex-shrink-0 mt-[0.125rem]">
                    <span class="tabular-nums flex items-baseline gap-1">
                      <span class="text-[15px] font-medium text-base-content/75">{{ collection.document_count || 0 }}</span>
                      <span class="text-[10px] uppercase tracking-wider text-base-content/35">{{ (collection.document_count || 0) === 1 ? 'doc' : 'docs' }}</span>
                    </span>

                    <div class="flex items-center gap-0.5 opacity-0 group-hover:opacity-100 focus-within:opacity-100 transition-opacity">
                      <button
                        v-if="userStore.privateCollections && collection.permission === 'owner' && !collection.team"
                        @click.stop="openShareModal(collection)"
                        class="btn btn-ghost btn-xs btn-square"
                        title="Share collection"
                        :aria-label="`Share collection ${collection.name}`"
                      >
                        <Share2 :size="13" />
                      </button>
                      <button
                        @click.stop="openEditCollectionModal(collection)"
                        class="btn btn-ghost btn-xs btn-square"
                        title="Edit collection"
                        :aria-label="`Edit collection ${collection.name}`"
                        :disabled="collection.shared && collection.permission === 'read'"
                      >
                        <Pencil :size="13" />
                      </button>
                    </div>
                  </div>
                </li>
                </template>
              </ul>

              <!-- Card view -->
              <div
                v-else
                class="grid gap-3"
                style="grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));"
              >
                <div
                  v-for="collection in filteredCollections"
                  :key="collection.id"
                  class="group relative flex flex-col gap-3 p-5 rounded-lg border border-base-300/60 bg-base-100 hover:border-base-content/25 hover:bg-base-100 transition-colors cursor-pointer min-h-[148px]"
                  :class="{ 'ring-1 ring-base-content/20': collection.id === collectionStore.currentCollectionId }"
                  @click="selectCollectionAndNavigate(collection.id)"
                  :aria-current="collection.id === collectionStore.currentCollectionId ? 'true' : undefined"
                  role="button"
                  tabindex="0"
                  @keydown.enter="selectCollectionAndNavigate(collection.id)"
                  @keydown.space.prevent="selectCollectionAndNavigate(collection.id)"
                >
                  <div class="flex items-start gap-2.5">
                    <span
                      class="mt-[0.45rem] w-2 h-2 rounded-full flex-shrink-0 ring-4 ring-transparent transition-all"
                      :class="{ 'ring-base-200': collection.id === collectionStore.currentCollectionId }"
                      :style="{ backgroundColor: collection.color }"
                      aria-hidden="true"
                    ></span>
                    <div class="min-w-0 flex-1">
                      <h3
                        class="text-[15px] truncate group-hover:text-primary transition-colors"
                        :class="collection.id === collectionStore.currentCollectionId ? 'font-semibold' : 'font-medium'"
                      >{{ collection.name }}</h3>
                      <div class="flex gap-2 mt-0.5 min-h-[12px]">
                        <span
                          v-if="collection.id === collectionStore.currentCollectionId"
                          class="text-[10px] uppercase tracking-[0.14em] font-semibold text-base-content/55"
                        >Active</span>
                        <span
                          v-if="collection.shared"
                          class="text-[10px] uppercase tracking-[0.14em] text-base-content/40"
                        >Shared</span>
                        <span
                          v-else-if="userStore.privateCollections && collection.team"
                          class="text-[10px] uppercase tracking-[0.14em] text-base-content/40"
                        >Team</span>
                        <span
                          v-else-if="userStore.privateCollections"
                          class="text-[10px] uppercase tracking-[0.14em] text-base-content/40"
                        >Private</span>
                      </div>
                    </div>
                  </div>

                  <p
                    v-if="collection.description"
                    class="text-[13px] leading-relaxed text-base-content/55 line-clamp-3 flex-1"
                  >{{ collection.description }}</p>
                  <div v-else class="flex-1"></div>

                  <div class="flex items-center justify-between pt-1 border-t border-base-300/40 -mx-1 px-1">
                    <span class="tabular-nums flex items-baseline gap-1">
                      <span class="text-[15px] font-medium text-base-content/75">{{ collection.document_count || 0 }}</span>
                      <span class="text-[10px] uppercase tracking-wider text-base-content/35">{{ (collection.document_count || 0) === 1 ? 'doc' : 'docs' }}</span>
                    </span>
                    <div class="flex items-center gap-0.5 opacity-0 group-hover:opacity-100 focus-within:opacity-100 transition-opacity">
                      <button
                        v-if="userStore.privateCollections && collection.permission === 'owner' && !collection.team"
                        @click.stop="openShareModal(collection)"
                        class="btn btn-ghost btn-xs btn-square"
                        title="Share collection"
                        :aria-label="`Share collection ${collection.name}`"
                      >
                        <Share2 :size="13" />
                      </button>
                      <button
                        @click.stop="openEditCollectionModal(collection)"
                        class="btn btn-ghost btn-xs btn-square"
                        title="Edit collection"
                        :aria-label="`Edit collection ${collection.name}`"
                        :disabled="collection.shared && collection.permission === 'read'"
                      >
                        <Pencil :size="13" />
                      </button>
                    </div>
                  </div>
                </div>
              </div>
            </div>

            <SearchTab v-if="activeTab === 'search'" @switch-tab="switchTab" />
            <ArtifactsTab v-if="activeTab === 'generate'" />
            <ExpertiseLibrary v-if="activeTab === 'expertise'" />
            <MCPTab v-if="activeTab === 'mcp'" />
            <AdminTab v-if="activeTab === 'admin'" />
            <SettingsTab v-if="activeTab === 'settings'" @data-cleared="handleDataCleared" @stats-updated="statsStore.fetchStats" @switch-tab="switchTab" @chat-tab-toggled="onChatTabToggled" />
          </div>
        </div>

      </main>

      <!-- Right sidebar: Studio (resizable, hidden on collections overview) -->
      <aside
        v-show="!isCollectionsView"
        class="bg-base-100 overflow-hidden flex flex-col border-l border-base-300"
        :class="[
          isResizingAnalysis ? '' : 'transition-all duration-200 ease-in-out',
          isCompact ? 'absolute right-0 top-0 h-full z-40 shadow-2xl' : 'flex-shrink-0 relative',
        ]"
        :style="{ width: analysisSidebarOpen ? effectiveAnalysisWidth + 'px' : '0px' }"
      >
        <!-- Drag handle (on the LEFT edge of the right sidebar; pointless in compact overlay mode) -->
        <div
          v-if="analysisSidebarOpen && !isCompact"
          class="absolute left-0 top-0 h-full w-1 cursor-col-resize group z-10 hover:bg-primary/40 transition-colors"
          :class="isResizingAnalysis ? 'bg-primary/60' : ''"
          @mousedown.prevent="startResizeAnalysis"
        >
          <div class="absolute inset-y-0 left-0 flex items-center justify-center w-1">
            <div class="flex flex-col gap-1 opacity-0 group-hover:opacity-60 transition-opacity">
              <div class="w-1 h-1 rounded-full bg-base-content"></div>
              <div class="w-1 h-1 rounded-full bg-base-content"></div>
              <div class="w-1 h-1 rounded-full bg-base-content"></div>
            </div>
          </div>
        </div>

        <div class="h-full flex flex-col" :style="{ width: effectiveAnalysisWidth + 'px' }">
          <StudioSidebar
            @close="analysisSidebarOpen = false"
            @send-to-chat="handleSendToChat"
            @switch-tab="switchTab"
          />
        </div>
      </aside>
    </div>

    <!-- ── Footer status bar (background jobs) ── -->
    <footer
      class="flex items-center gap-3 px-3 h-6 bg-base-100 border-t border-base-300 text-[11px] text-base-content/55 flex-shrink-0"
      role="contentinfo"
      aria-label="Background jobs status"
    >
      <button
        class="flex items-center gap-1.5 hover:text-base-content transition-colors"
        @click="showJobsDrawer = true"
        :title="backgroundJobsStore.activeJobCount > 0 ? `${backgroundJobsStore.activeJobCount} active job${backgroundJobsStore.activeJobCount === 1 ? '' : 's'} — click to open` : 'Background jobs — click to open'"
        :aria-label="backgroundJobsStore.activeJobCount > 0
          ? `Background jobs — ${backgroundJobsStore.activeJobCount} active`
          : 'Background jobs'"
      >
        <Loader2
          v-if="backgroundJobsStore.hasActiveJobs"
          :size="11"
          class="animate-spin text-warning"
          aria-hidden="true"
        />
        <Bell v-else :size="11" aria-hidden="true" />
        <span v-if="backgroundJobsStore.activeJobCount > 0" class="font-medium text-warning">
          {{ backgroundJobsStore.activeJobCount }} job{{ backgroundJobsStore.activeJobCount === 1 ? '' : 's' }} running
        </span>
        <span v-else>Idle</span>
      </button>

      <!-- Inline progress for the most recent active job, if any -->
      <template v-if="footerActiveJob">
        <span class="w-px h-3 bg-base-300" aria-hidden="true"></span>
        <span class="truncate max-w-[40ch] text-base-content/45">
          {{ footerActiveJob.currentFile || footerActiveJob.phase || footerActiveJob.type }}
        </span>
        <progress
          class="progress progress-warning w-24 h-1.5"
          :value="footerActiveJob.progress || 0"
          max="100"
          aria-hidden="true"
        ></progress>
      </template>

      <div class="flex-1"></div>

      <!-- Air-gapped deployment indicator (OFFLINE_MODE=1 on the server) -->
      <span
        v-if="statsStore.offline"
        class="hidden sm:flex items-center gap-1 text-base-content/60"
        title="Offline mode: cloud AI disabled — no external connections beyond your configured endpoints"
      >
        <ShieldCheck :size="11" aria-hidden="true" />
        Air-gapped
      </span>
      <span v-if="statsStore.offline" class="hidden sm:inline w-px h-3 bg-base-300" aria-hidden="true"></span>

      <!-- Stats moved here from the header for breathing room -->
      <span class="hidden md:inline tabular-nums">
        {{ statsStore.documents }} {{ statsStore.documents === 1 ? 'source' : 'sources' }}
      </span>
      <span class="hidden md:inline w-px h-3 bg-base-300" aria-hidden="true"></span>
      <span class="hidden md:inline tabular-nums">
        {{ statsStore.pages }} {{ statsStore.pages === 1 ? 'page' : 'pages' }}
      </span>
    </footer>

    <!-- Global toast stack + backend-unreachable banner -->
    <Toaster />

    <!-- Create Collection Modal (native showModal: focus trap, Escape, inert background) -->
    <dialog :ref="createModal.dialogRef" class="modal" @close="createModal.onClosed" aria-labelledby="create-collection-title">
      <div class="modal-box">
        <h3 id="create-collection-title" class="font-bold text-lg mb-4">Create New Collection</h3>

        <div class="form-control w-full mb-4">
          <label class="label" for="new-collection-name">
            <span class="label-text">Collection Name</span>
          </label>
          <input
            id="new-collection-name"
            v-model="newCollectionName"
            type="text"
            placeholder="e.g., Research Papers"
            class="input input-bordered w-full"
            @keyup.enter="createCollection"
          />
        </div>

        <div class="form-control w-full mb-4">
          <label class="label" for="new-collection-description">
            <span class="label-text">Description (optional)</span>
          </label>
          <textarea
            id="new-collection-description"
            v-model="newCollectionDescription"
            class="textarea textarea-bordered"
            placeholder="What kind of sources will this collection contain?"
          ></textarea>
        </div>

        <div v-if="userStore.privateCollections" class="form-control w-full mb-4">
          <label class="label pb-1">
            <span class="label-text">Visibility</span>
          </label>
          <div class="flex flex-col gap-2" role="radiogroup" aria-label="Collection visibility">
            <label class="flex items-start gap-3 p-3 rounded-lg border cursor-pointer transition-colors"
                   :class="newCollectionVisibility === 'private' ? 'border-primary bg-primary/5' : 'border-base-300'">
              <input type="radio" value="private" v-model="newCollectionVisibility" class="radio radio-primary radio-sm mt-0.5" />
              <span>
                <span class="block text-sm font-medium">Private</span>
                <span class="block text-xs text-base-content/55">Only you can see it. Share it later with read or read-write links.</span>
              </span>
            </label>
            <label class="flex items-start gap-3 p-3 rounded-lg border cursor-pointer transition-colors"
                   :class="newCollectionVisibility === 'team' ? 'border-primary bg-primary/5' : 'border-base-300'">
              <input type="radio" value="team" v-model="newCollectionVisibility" class="radio radio-primary radio-sm mt-0.5" />
              <span>
                <span class="block text-sm font-medium">Team</span>
                <span class="block text-xs text-base-content/55">Everyone on this deployment can see and edit it.</span>
              </span>
            </label>
          </div>
        </div>

        <div class="form-control w-full mb-4">
          <label class="label" for="new-collection-color">
            <span class="label-text">Color</span>
          </label>
          <div class="flex items-center gap-3">
            <input
              id="new-collection-color"
              v-model="newCollectionColor"
              type="color"
              class="w-12 h-12 rounded cursor-pointer border-2 border-base-300"
              aria-label="Custom collection color"
            />
            <div class="flex gap-2" role="radiogroup" aria-label="Preset collection colors">
              <button
                v-for="color in ['#3b82f6', '#10b981', '#f59e0b', '#ef4444', '#8b5cf6', '#ec4899']"
                :key="color"
                type="button"
                role="radio"
                :aria-checked="newCollectionColor === color"
                :aria-label="`Color ${color}`"
                class="w-8 h-8 rounded cursor-pointer border-2"
                :class="newCollectionColor === color ? 'border-base-content' : 'border-transparent'"
                :style="{ backgroundColor: color }"
                @click="newCollectionColor = color"
              ></button>
            </div>
          </div>
        </div>

        <div class="modal-action">
          <button class="btn btn-ghost" @click="createModal.close()" :disabled="creatingCollection">
            Cancel
          </button>
          <button
            class="btn btn-primary"
            @click="createCollection"
            :disabled="!newCollectionName.trim() || creatingCollection"
          >
            <span v-if="creatingCollection" class="loading loading-spinner loading-sm"></span>
            Create Collection
          </button>
        </div>
      </div>
      <form method="dialog" class="modal-backdrop">
        <button>close</button>
      </form>
    </dialog>

    <!-- Edit Collection Modal -->
    <dialog :ref="editModal.dialogRef" class="modal" @close="editModal.onClosed" aria-labelledby="edit-collection-title">
      <div class="modal-box">
        <h3 id="edit-collection-title" class="font-bold text-lg mb-4">Edit Collection</h3>

        <div class="form-control w-full mb-4">
          <label class="label" for="edit-collection-name">
            <span class="label-text">Collection Name</span>
          </label>
          <input
            id="edit-collection-name"
            v-model="editCollectionName"
            type="text"
            placeholder="e.g., Research Papers"
            class="input input-bordered w-full"
            :disabled="editingCollectionId === 'default'"
            :aria-describedby="editingCollectionId === 'default' ? 'edit-collection-name-help' : undefined"
          />
          <p v-if="editingCollectionId === 'default'" id="edit-collection-name-help" class="label-text-alt text-warning mt-1">
            Default collection name cannot be changed
          </p>
        </div>

        <div class="form-control w-full mb-4">
          <label class="label pb-1" for="edit-collection-description">
            <span class="label-text">Description</span>
          </label>
          <textarea
            id="edit-collection-description"
            v-model="editCollectionDescription"
            class="textarea textarea-bordered w-full"
            rows="2"
            placeholder="What kind of sources does this collection contain?"
          ></textarea>
        </div>

        <div class="form-control w-full mb-4">
          <label class="label pb-1" for="edit-collection-guide">
            <span class="label-text">Guide for calling agents</span>
          </label>
          <textarea
            id="edit-collection-guide"
            v-model="editCollectionGuide"
            class="textarea textarea-bordered w-full font-mono text-sm"
            rows="6"
            placeholder="e.g. currency is USD; 'Jane' = Jane Smith; dates are MM/DD/YYYY; 'NAV' column is market value net of fees."
          ></textarea>
          <p class="text-xs opacity-70 mt-1">
            Surfaced in every MCP response (~500 char summary in search, full text in get_collection_info) so the calling LLM has durable context.
          </p>
        </div>

        <div class="form-control w-full mb-4">
          <label class="label" for="edit-collection-color">
            <span class="label-text">Color</span>
          </label>
          <div class="flex items-center gap-3">
            <input
              id="edit-collection-color"
              v-model="editCollectionColor"
              type="color"
              class="w-12 h-12 rounded cursor-pointer border-2 border-base-300"
              aria-label="Custom collection color"
            />
            <div class="flex gap-2" role="radiogroup" aria-label="Preset collection colors">
              <button
                v-for="color in ['#3b82f6', '#10b981', '#f59e0b', '#ef4444', '#8b5cf6', '#ec4899']"
                :key="color"
                type="button"
                role="radio"
                :aria-checked="editCollectionColor === color"
                :aria-label="`Color ${color}`"
                class="w-8 h-8 rounded cursor-pointer border-2"
                :class="editCollectionColor === color ? 'border-base-content' : 'border-transparent'"
                :style="{ backgroundColor: color }"
                @click="editCollectionColor = color"
              ></button>
            </div>
          </div>
        </div>

        <div class="modal-action justify-between">
          <button
            v-if="editingCollectionId !== 'default'"
            class="btn btn-error btn-outline"
            @click="confirmDeleteCollection"
            :disabled="updatingCollection"
          >
            <Trash2 :size="16" />
            Delete
          </button>
          <div v-else></div>
          <div class="flex gap-2">
            <button class="btn btn-ghost" @click="editModal.close()" :disabled="updatingCollection">
              Cancel
            </button>
            <button
              class="btn btn-primary"
              @click="updateCollection"
              :disabled="!editCollectionName.trim() || updatingCollection"
            >
              <span v-if="updatingCollection" class="loading loading-spinner loading-sm"></span>
              Save Changes
            </button>
          </div>
        </div>
      </div>
      <form method="dialog" class="modal-backdrop">
        <button>close</button>
      </form>
    </dialog>

    <!-- Delete Collection Confirmation Modal -->
    <dialog
      :ref="deleteModal.dialogRef"
      class="modal"
      @close="deleteModal.onClosed"
      aria-labelledby="delete-collection-title"
      aria-describedby="delete-collection-desc"
    >
      <div class="modal-box">
        <h3 id="delete-collection-title" class="font-bold text-lg text-error mb-4">Delete Collection</h3>
        <p id="delete-collection-desc" class="mb-2">Are you sure you want to delete <strong>{{ editCollectionName }}</strong>?</p>

        <div class="alert alert-warning my-4">
          <svg xmlns="http://www.w3.org/2000/svg" class="stroke-current shrink-0 h-6 w-6" fill="none" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
          </svg>
          <div>
            <div class="font-bold">This will permanently delete:</div>
            <ul class="list-disc list-inside text-sm mt-1">
              <li>All uploaded sources and files</li>
              <li>All vector indexes and embeddings</li>
              <li>All search history for this collection</li>
            </ul>
            <div class="text-sm font-semibold mt-2">This action cannot be undone.</div>
          </div>
        </div>

        <!-- Error display -->
        <div v-if="deleteError" class="alert alert-error mb-4">
          <svg xmlns="http://www.w3.org/2000/svg" class="stroke-current shrink-0 h-6 w-6" fill="none" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10 14l2-2m0 0l2-2m-2 2l-2-2m2 2l2 2m7-2a9 9 0 11-18 0 9 9 0 0118 0z" />
          </svg>
          <span>{{ deleteError }}</span>
        </div>

        <div class="modal-action">
          <button class="btn btn-ghost" @click="closeDeleteModal" :disabled="deletingCollection">
            Cancel
          </button>
          <button
            class="btn btn-error"
            @click="deleteCollection"
            :disabled="deletingCollection"
          >
            <span v-if="deletingCollection" class="loading loading-spinner loading-sm"></span>
            Delete Collection
          </button>
        </div>
      </div>
      <form method="dialog" class="modal-backdrop">
        <button>close</button>
      </form>
    </dialog>

    <!-- Share Modal -->
    <ShareModal
      :visible="showShareModal"
      :collection-id="shareCollectionId"
      :collection-name="shareCollectionName"
      :initial-token="shareInitialToken"
      @close="showShareModal = false; shareInitialToken = ''"
      @shared="handleShared"
    />

    <!-- First-run onboarding takeover (shown only when no provider is configured) -->
    <WelcomeOnboarding
      :show="showOnboarding"
      @complete="handleOnboardingComplete"
      @skip="handleOnboardingSkip"
    />

    <!-- Background Jobs Sidebar Drawer -->
    <div
      v-if="showJobsDrawer"
      class="fixed inset-0 z-[200]"
      @click.self="showJobsDrawer = false"
      role="dialog"
      aria-modal="true"
      aria-labelledby="jobs-drawer-title"
    >
      <!-- Backdrop -->
      <div class="absolute inset-0 bg-base-content/20" @click="showJobsDrawer = false" aria-hidden="true"></div>

      <!-- Drawer Panel -->
      <div class="absolute right-0 top-0 h-full w-96 max-w-[90vw] bg-base-100 shadow-2xl flex flex-col">
        <!-- Header -->
        <div class="flex items-center justify-between p-4 border-b border-base-300">
          <h3 id="jobs-drawer-title" class="text-lg font-bold">Background Jobs</h3>
          <button
            class="btn btn-ghost btn-sm btn-circle"
            @click="showJobsDrawer = false"
            aria-label="Close background jobs panel"
          >
            <X :size="20" />
          </button>
        </div>

        <!-- Content -->
        <div class="flex-1 overflow-y-auto p-4">
          <div v-if="backgroundJobsStore.allJobs.length === 0" class="text-center py-12 text-base-content/50">
            <Bell :size="48" class="mx-auto mb-4 opacity-30" />
            <p>No background jobs</p>
            <p class="text-sm mt-1">Large file uploads will appear here</p>
          </div>

          <div v-else class="space-y-4">
            <div
              v-for="job in backgroundJobsStore.allJobs"
              :key="`${job.type}-${job.id}`"
              class="card bg-base-200"
            >
              <div class="card-body p-4">
                <!-- Job Header -->
                <div class="flex items-center justify-between">
                  <div class="flex items-center gap-2">
                    <Loader2
                      v-if="job.status === 'pending' || job.status === 'running'"
                      :size="18"
                      class="animate-spin text-primary"
                    />
                    <CheckCircle
                      v-else-if="job.status === 'completed'"
                      :size="18"
                      class="text-success"
                    />
                    <XCircle
                      v-else-if="job.status === 'failed' || job.status === 'cancelled'"
                      :size="18"
                      class="text-error"
                    />
                    <span class="font-semibold capitalize">
                      {{ job.type === 'index' ? 'Indexing' : job.type === 'upload' ? 'Upload' : job.type }}
                    </span>
                    <span class="badge badge-sm" :class="{
                      'badge-warning': job.status === 'pending',
                      'badge-info': job.status === 'running',
                      'badge-success': job.status === 'completed',
                      'badge-error': job.status === 'failed' || job.status === 'cancelled'
                    }">{{ job.status }}</span>
                  </div>
                  <button
                    v-if="(job.type === 'upload' || job.type === 'index') && (job.status === 'pending' || job.status === 'running')"
                    @click="cancelJob(job.id)"
                    class="btn btn-ghost btn-xs text-error"
                    title="Cancel job"
                    :aria-label="`Cancel ${job.type} job`"
                  >
                    <XCircle :size="16" />
                  </button>
                </div>

                <!-- Current File -->
                <div v-if="job.currentFile" class="text-sm text-base-content/70 truncate">
                  {{ job.currentFile }}
                </div>

                <!-- Phase Info -->
                <div v-if="job.phase && (job.status === 'running' || job.status === 'pending')" class="flex items-center gap-2 text-sm">
                  <span class="text-base-content/50">Phase:</span>
                  <span class="badge badge-sm badge-primary capitalize">{{ job.phase }}</span>
                  <span v-if="job.chunksTotal > 0" class="text-base-content/50">
                    ({{ job.chunksProcessed }}/{{ job.chunksTotal }} chunks)
                  </span>
                </div>

                <!-- Progress -->
                <div v-if="job.status === 'running' || job.status === 'pending'" class="space-y-1">
                  <progress
                    class="progress progress-primary w-full"
                    :value="job.progress"
                    max="100"
                  ></progress>
                  <div class="flex justify-between text-xs text-base-content/60">
                    <span>{{ Math.round(job.progress) }}%</span>
                    <span>{{ job.processedFiles || 0 }}/{{ job.totalFiles || '?' }} files</span>
                  </div>
                </div>

                <!-- Error -->
                <div v-if="job.error" class="text-sm text-error">
                  {{ job.error }}
                </div>
              </div>
            </div>
          </div>
        </div>

        <!-- Footer -->
        <div v-if="backgroundJobsStore.allJobs.some(j => j.status === 'completed' || j.status === 'failed' || j.status === 'cancelled')" class="p-4 border-t border-base-300">
          <button class="btn btn-ghost btn-sm w-full" @click="clearCompletedJobs">
            Clear completed jobs
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted, onBeforeUnmount, watch, computed, defineAsyncComponent } from 'vue'
import { Search, Settings, Plus, Check, ChevronDown, Pencil, Trash2, Bell, Loader2, CheckCircle, XCircle, X, PanelRightOpen, MessageSquare, Library, Share2, Users, Plug, LayoutGrid, List, BookOpen, Sparkles, ShieldCheck, CircleUser, LogOut, Gauge, HelpCircle } from 'lucide-vue-next'
import http from './utils/http'
import { useModal } from './composables/useModal'

const chatTabEnabled = ref(true)

const tabs = computed(() => {
  const t = []
  if (chatTabEnabled.value) t.push({ id: 'chat', label: 'Chat', icon: MessageSquare })
  t.push({ id: 'search', label: 'Search', icon: Search })
  t.push({ id: 'generate', label: 'Generate', icon: Sparkles })
  t.push({ id: 'expertise', label: 'Expertise', icon: BookOpen })
  t.push({ id: 'mcp', label: 'MCP', icon: Plug })
  // Operator console: usage, admissions, live stats. Admin-only — the
  // backend enforces it too; hiding the tab just avoids a 403 surprise.
  if (userStore.isAdmin) t.push({ id: 'admin', label: 'Admin', icon: Gauge })
  return t
})

// Core layout + default tab load eagerly; every other tab is code-split so
// the initial bundle stays small.
import SourcesSidebar from './components/SourcesSidebar.vue'
import StudioSidebar from './components/StudioSidebar.vue'
import ChatTab from './components/ChatTab.vue'

const SearchTab = defineAsyncComponent(() => import('./components/SearchTab.vue'))
const ArtifactsTab = defineAsyncComponent(() => import('./components/ArtifactsTab.vue'))
const SettingsTab = defineAsyncComponent(() => import('./components/SettingsTab.vue'))
const MCPTab = defineAsyncComponent(() => import('./components/MCPTab.vue'))
const ShareModal = defineAsyncComponent(() => import('./components/ShareModal.vue'))
const ExpertiseLibrary = defineAsyncComponent(() => import('./components/ExpertiseLibrary.vue'))
const WelcomeOnboarding = defineAsyncComponent(() => import('./components/WelcomeOnboarding.vue'))
const AdminTab = defineAsyncComponent(() => import('./components/AdminTab.vue'))
import Toaster from './components/Toaster.vue'
import {
  getConfiguredProviderIds,
  getServerProviderIds,
  setActiveProviderLS,
  getProviderDisplayName,
  getProviderConfig,
  migrateLegacySettings,
} from './utils/aiProviders.js'
import { useCollectionStore } from './stores/collectionStore'
import { useUserStore } from './stores/userStore'
import { useSearchStore } from './stores/searchStore'
import { useBackgroundJobsStore } from './stores/backgroundJobsStore'
import { useProviderStore } from './stores/providerStore'
import { useStatsStore } from './stores/statsStore'
import { useUiStore } from './stores/uiStore'

// Fold legacy provider keys (chat_provider, ai_api_key_*) into the unified
// config before any tab mounts and reads it.
migrateLegacySettings()

const collectionStore = useCollectionStore()
const searchStore = useSearchStore()
const backgroundJobsStore = useBackgroundJobsStore()
const userStore = useUserStore()
const providerStore = useProviderStore()
const statsStore = useStatsStore()
const ui = useUiStore()

// ── Global provider pill (top bar) ──
// Shows the app-wide default provider resolved by the shared chain in
// aiProviders.js; the dropdown switches ai_settings.provider directly.
// Reactivity comes from providerStore's version — every provider config
// write bumps it, so this recomputes without any event listeners here.
const providerPill = computed(() => {
  providerStore.version // reactivity hook
  const configured = getConfiguredProviderIds()
  const id = providerStore.resolveFor()
  return {
    id,
    configured,
    teamIds: getServerProviderIds(),
    name: id ? getProviderDisplayName(id) : '',
    model: (id && getProviderConfig(id)?.model) || '',
  }
})

const providerDisplayName = getProviderDisplayName

const setGlobalProvider = (pid) => {
  setActiveProviderLS(pid) // dispatches the change event → pill refreshes
  if (document.activeElement instanceof HTMLElement) document.activeElement.blur()
}

// Restore the last active tab so a refresh doesn't dump the user back in Chat
const VALID_TABS = ['chat', 'search', 'generate', 'expertise', 'mcp', 'admin', 'settings', 'collections']
const savedTab = localStorage.getItem('active_tab')
const activeTab = ref(VALID_TABS.includes(savedTab) ? savedTab : 'chat')
watch(activeTab, (tab) => localStorage.setItem('active_tab', tab))

// Resizable sidebar
const SIDEBAR_MIN = 220
const SIDEBAR_MAX = 600
const sidebarWidth = ref(parseInt(localStorage.getItem('sidebar_width') || '320'))
const isResizing = ref(false)

const startResize = (e) => {
  isResizing.value = true
  const startX = e.clientX
  const startWidth = sidebarWidth.value

  const onMove = (e) => {
    const delta = e.clientX - startX
    sidebarWidth.value = Math.min(SIDEBAR_MAX, Math.max(SIDEBAR_MIN, startWidth + delta))
  }

  const onUp = () => {
    isResizing.value = false
    localStorage.setItem('sidebar_width', String(sidebarWidth.value))
    window.removeEventListener('mousemove', onMove)
    window.removeEventListener('mouseup', onUp)
  }

  window.addEventListener('mousemove', onMove)
  window.addEventListener('mouseup', onUp)
}

// Signed-in identity (Cloudflare Access deployments); drives the account menu.
const me = ref({ authenticated_via: null, identity: null, logout_url: null })
const loadMe = async () => {
  try {
    const resp = await http.get('/api/me')
    me.value = resp.data
  } catch { /* open or password deployments simply show no account menu */ }
}

// Compact (sidebar/companion) mode: when the window is pinned narrow next to
// other apps, the side panels overlay the main surface instead of squeezing
// it. Tracked live so dragging the window across the threshold adapts.
const compactQuery = window.matchMedia('(max-width: 767px)')
const isCompact = ref(compactQuery.matches)
compactQuery.addEventListener('change', e => { isCompact.value = e.matches })

// Sources sidebar state (persisted; starts closed in compact mode where an
// open overlay would cover the whole answer surface)
const sourcesSidebarOpen = ref(
  isCompact.value ? false : localStorage.getItem('sources_sidebar_open') !== 'false'
)

watch(sourcesSidebarOpen, v => {
  if (!isCompact.value) localStorage.setItem('sources_sidebar_open', String(v))
  if (v && isCompact.value) analysisSidebarOpen.value = false  // one overlay at a time
})

// Analysis sidebar state (persisted, resizable)
const ANALYSIS_MIN = 240
const ANALYSIS_MAX = 600
const analysisWidth = ref(parseInt(localStorage.getItem('analysis_width') || '320'))
const analysisSidebarOpen = ref(
  isCompact.value ? false : localStorage.getItem('analysis_sidebar_open') !== 'false'
)
const isResizingAnalysis = ref(false)

watch(analysisSidebarOpen, v => {
  if (!isCompact.value) localStorage.setItem('analysis_sidebar_open', String(v))
  if (v && isCompact.value) sourcesSidebarOpen.value = false  // one overlay at a time
})

// Overlay panels cap at 85% of the window so a sliver of context stays visible
const compactPanelCap = () => Math.round(window.innerWidth * 0.85)
const effectiveSidebarWidth = computed(() =>
  isCompact.value ? Math.min(sidebarWidth.value, compactPanelCap()) : sidebarWidth.value
)
const effectiveAnalysisWidth = computed(() =>
  isCompact.value ? Math.min(analysisWidth.value, compactPanelCap()) : analysisWidth.value
)

// Entering compact mode with both panels open would stack two overlays; close them.
watch(isCompact, compact => {
  if (compact) {
    sourcesSidebarOpen.value = false
    analysisSidebarOpen.value = false
  } else {
    sourcesSidebarOpen.value = localStorage.getItem('sources_sidebar_open') !== 'false'
    analysisSidebarOpen.value = localStorage.getItem('analysis_sidebar_open') !== 'false'
  }
})

const startResizeAnalysis = (e) => {
  isResizingAnalysis.value = true
  const startX = e.clientX
  const startWidth = analysisWidth.value

  const onMove = (e) => {
    // Right sidebar grows when dragging LEFT, so invert
    const delta = startX - e.clientX
    analysisWidth.value = Math.min(ANALYSIS_MAX, Math.max(ANALYSIS_MIN, startWidth + delta))
  }

  const onUp = () => {
    isResizingAnalysis.value = false
    localStorage.setItem('analysis_width', String(analysisWidth.value))
    window.removeEventListener('mousemove', onMove)
    window.removeEventListener('mouseup', onUp)
  }

  window.addEventListener('mousemove', onMove)
  window.addEventListener('mouseup', onUp)
}

// True when the user is on the all-collections overview — sidebars hide here.
const isCollectionsView = computed(() => activeTab.value === 'collections')

// Most recent active job, surfaced inline in the footer.
const footerActiveJob = computed(() =>
  backgroundJobsStore.allJobs.find(j => j.status === 'running' || j.status === 'pending')
)

// Forward an Analysis-sidebar action into the chat input.
const handleSendToChat = (prompt) => {
  if (!chatTabEnabled.value) return
  activeTab.value = 'chat'
  // Wait one tick so ChatTab is mounted before we deliver the prompt.
  setTimeout(() => {
    window.dispatchEvent(new CustomEvent('asymptote:prefill-chat', { detail: { prompt } }))
  }, 50)
}

// Create collection modal (native <dialog> via useModal: focus trap,
// Escape, focus restore)
const createModal = useModal()
const newCollectionName = ref('')
const newCollectionDescription = ref('')
const newCollectionColor = ref('#3b82f6')
const newCollectionVisibility = ref('private')
const creatingCollection = ref(false)

// Edit collection modal
const editModal = useModal()
const editingCollectionId = ref('')
const editCollectionName = ref('')
const editCollectionDescription = ref('')
const editCollectionColor = ref('#3b82f6')
const editCollectionGuide = ref('')
const updatingCollection = ref(false)

// Delete confirmation modal
const deleteModal = useModal()
const deletingCollection = ref(false)
const deleteError = ref('')

// Share modal state
const showShareModal = ref(false)
const shareCollectionId = ref('')
const shareCollectionName = ref('')
const shareInitialToken = ref('')

// Background jobs drawer state
const showJobsDrawer = ref(false)

// First-run onboarding: a full-screen takeover shown when the user has
// never configured an AI provider. Resolves the "you installed the app but
// nothing works until you curl an endpoint" problem we hit earlier.
const showOnboarding = ref(false)

const checkOnboardingNeeded = () => {
  // If any provider is already configured in localStorage, skip onboarding.
  // We intentionally don't also check server-side embedding keys — the
  // primary gate is "can this user have a chat conversation yet."
  // Users who pre-configured a chat provider via another path (e.g. the
  // MCP setup flow) shouldn't be blocked by this screen.
  showOnboarding.value = getConfiguredProviderIds().length === 0
}

const handleOnboardingComplete = () => {
  showOnboarding.value = false
  // Provider connected — the actual next blocker is having zero sources.
  // Point at the sidebar instead of dropping the user on an empty chat.
  if (statsStore.documents === 0) {
    sourcesSidebarOpen.value = true
    ui.highlightAddSources = true
    ui.notify('AI connected. Next: add your first sources in the left panel.', 'success', { duration: 8000 })
  }
}

const handleOnboardingSkip = () => {
  showOnboarding.value = false
  activeTab.value = 'settings'
}

// Collections overview: view, search, sort
const collectionsView = ref(localStorage.getItem('collections_view') || 'list')
const collectionsSort = ref(localStorage.getItem('collections_sort') || 'recent')
const collectionsSearch = ref('')

const collectionsSortOptions = [
  { id: 'recent', label: 'Most recent' },
  { id: 'name', label: 'Name' },
  { id: 'docs', label: 'Most documents' },
]

const collectionsSortLabel = computed(
  () => collectionsSortOptions.find(o => o.id === collectionsSort.value)?.label || ''
)

watch(collectionsView, v => localStorage.setItem('collections_view', v))
watch(collectionsSort, v => localStorage.setItem('collections_sort', v))

// Reset search each time the user enters the collections tab
watch(activeTab, tab => {
  if (tab === 'collections') collectionsSearch.value = ''
})

const filteredCollections = computed(() => {
  const q = collectionsSearch.value.trim().toLowerCase()
  let list = [...collectionStore.sortedCollections]

  if (q) {
    list = list.filter(c =>
      c.name.toLowerCase().includes(q) ||
      (c.description && c.description.toLowerCase().includes(q))
    )
  }

  if (collectionsSort.value === 'name') {
    list.sort((a, b) => a.name.localeCompare(b.name))
  } else if (collectionsSort.value === 'docs') {
    list.sort((a, b) => (b.document_count || 0) - (a.document_count || 0))
  } else if (collectionsSort.value === 'recent') {
    list.sort((a, b) => {
      const ta = a.updated_at || a.created_at || ''
      const tb = b.updated_at || b.created_at || ''
      return tb.localeCompare(ta)
    })
  }

  // Pin the default collection first when not actively searching
  if (!q) {
    const i = list.findIndex(c => c.id === 'default')
    if (i > 0) list.unshift(list.splice(i, 1)[0])
  }

  // Stable partition: your collections first, then ones shared with you —
  // each group keeps the chosen sort order. The divider renders between.
  return [...list.filter(c => !c.shared), ...list.filter(c => c.shared)]
})

// Where the "Shared with me" divider goes in the overview (-1 = no divider:
// nothing shared, or everything shared).
const firstSharedIndex = computed(() => {
  const i = filteredCollections.value.findIndex(c => c.shared)
  return i > 0 ? i : -1
})

const updateThemeFromStorage = () => {
  const savedTheme = localStorage.getItem('theme')
  if (savedTheme) {
    document.documentElement.setAttribute('data-theme', savedTheme)
  } else {
    const prefersDark = window.matchMedia('(prefers-color-scheme: dark)').matches
    document.documentElement.setAttribute('data-theme', prefersDark ? 'dark' : 'light')
  }
}

const handleDocumentDeleted = async () => {
  // Reload both stats and collections list (for document_count in dropdown)
  await Promise.all([
    statsStore.fetchStats(),
    collectionStore.loadCollections()
  ])
}

const handleDataCleared = async () => {
  // Reload both stats and collections list (for document_count in dropdown)
  await Promise.all([
    statsStore.fetchStats(),
    collectionStore.loadCollections()
  ])
}

const onChatTabToggled = (enabled) => {
  chatTabEnabled.value = enabled
  if (!enabled && activeTab.value === 'chat') {
    activeTab.value = 'search'
  }
}

const switchTab = (tabName) => {
  activeTab.value = tabName
}

const selectCollectionAndNavigate = (collectionId) => {
  collectionStore.setCurrentCollection(collectionId)
  activeTab.value = chatTabEnabled.value ? 'chat' : 'search'
}

const openCreateCollectionModal = () => {
  newCollectionName.value = ''
  newCollectionDescription.value = ''
  newCollectionColor.value = '#3b82f6'
  newCollectionVisibility.value = 'private'
  createModal.open()
}

const createCollection = async () => {
  if (!newCollectionName.value.trim()) return

  creatingCollection.value = true
  try {
    const collection = await collectionStore.createCollection({
      name: newCollectionName.value.trim(),
      description: newCollectionDescription.value.trim(),
      color: newCollectionColor.value,
      visibility: newCollectionVisibility.value
    })
    collectionStore.setCurrentCollection(collection.id)
    createModal.close()
  } catch (err) {
    ui.toastError(err, 'Failed to create collection')
  } finally {
    creatingCollection.value = false
  }
}

const openEditCollectionModal = (collection) => {
  editingCollectionId.value = collection.id
  editCollectionName.value = collection.name
  editCollectionDescription.value = collection.description || ''
  editCollectionColor.value = collection.color || '#3b82f6'
  editCollectionGuide.value = collection.guide || ''
  editModal.open()
}

const updateCollection = async () => {
  if (!editCollectionName.value.trim()) return

  updatingCollection.value = true
  try {
    await collectionStore.updateCollection(editingCollectionId.value, {
      name: editingCollectionId.value === 'default' ? undefined : editCollectionName.value.trim(),
      description: editCollectionDescription.value.trim(),
      color: editCollectionColor.value,
      guide: editCollectionGuide.value
    })
    editModal.close()
  } catch (err) {
    ui.toastError(err, 'Failed to update collection')
  } finally {
    updatingCollection.value = false
  }
}

const openShareModal = (collection) => {
  shareCollectionId.value = collection.id
  shareCollectionName.value = collection.name
  showShareModal.value = true
}

// Accept-only mode: teammates who own nothing yet still need a way to paste
// a share token — ShareModal hides the create/list sections without an id.
const openJoinSharedModal = () => {
  shareCollectionId.value = ''
  shareCollectionName.value = ''
  showShareModal.value = true
}

const handleShared = () => {
  collectionStore.loadCollections()
}

const confirmDeleteCollection = () => {
  deleteError.value = ''
  deleteModal.open()
}

const closeDeleteModal = () => {
  if (!deletingCollection.value) {
    deleteModal.close()
    deleteError.value = ''
  }
}

const deleteCollection = async () => {
  deletingCollection.value = true
  deleteError.value = ''
  try {
    const deletedCollectionId = editingCollectionId.value
    const wasCurrentCollection = collectionStore.currentCollectionId === deletedCollectionId

    await collectionStore.deleteCollection(deletedCollectionId)
    // Clear search history for the deleted collection
    searchStore.clearCollectionCache(deletedCollectionId)

    // Close modals
    deleteModal.close()
    editModal.close()
    deleteError.value = ''

    // Switch to default collection if we deleted the current one
    if (wasCurrentCollection) {
      collectionStore.setCurrentCollection('default')
    }

    // Reload stats for the new current collection
    await statsStore.fetchStats()
  } catch (err) {
    deleteError.value = err.message || 'Failed to delete collection. Please try again.'
  } finally {
    deletingCollection.value = false
  }
}

// Watch for collection changes to reload stats
watch(() => collectionStore.currentCollectionId, () => {
  statsStore.fetchStats()
})

// Watch for background jobs completing to refresh collection counts and stats
watch(() => backgroundJobsStore.allJobs.map(j => j.status), (newStatuses, oldStatuses) => {
  // Check if any job just transitioned to completed
  if (oldStatuses && newStatuses.some((s, i) => s === 'completed' && oldStatuses[i] !== 'completed')) {
    collectionStore.loadCollections()
    statsStore.fetchStats()
  }
}, { deep: true })

// Live stats refresh while a job is still indexing into the current
// collection (throttled inside the jobs store), so the footer counts and
// chat gate update as documents land instead of only at job completion.
watch(() => backgroundJobsStore.dataRefreshTick, () => {
  if (backgroundJobsStore.dataRefreshCollectionId === collectionStore.currentCollectionId) {
    statsStore.fetchStatsDebounced()
  }
})

// Clear completed jobs from the drawer
const clearCompletedJobs = () => {
  // Remove completed upload jobs
  backgroundJobsStore.uploadJobs = backgroundJobsStore.uploadJobs.filter(
    j => j.status === 'pending' || j.status === 'running'
  )
  // Clear completed reindex job
  if (backgroundJobsStore.reindexJob &&
      (backgroundJobsStore.reindexJob.status === 'completed' || backgroundJobsStore.reindexJob.status === 'failed')) {
    backgroundJobsStore.clearReindexJob()
  }
}

// Cancel an active upload job
const cancelJob = async (jobId) => {
  try {
    await backgroundJobsStore.cancelUploadJob(jobId)
  } catch (err) {
    ui.toastError(err, 'Failed to cancel the job')
  }
}

onMounted(async () => {
  // Check whether we need the first-run onboarding takeover. Done first so
  // the screen paints immediately — the rest of the boot continues behind it.
  checkOnboardingNeeded()

  // Share-invitation deep link (?share_token=... from emailed invites):
  // open the join dialog with the token prefilled, then clean the URL so a
  // reload doesn't re-prompt.
  const shareToken = new URLSearchParams(window.location.search).get('share_token')
  if (shareToken) {
    shareInitialToken.value = shareToken
    openJoinSharedModal()
    window.history.replaceState({}, '', window.location.pathname)
  }

  // Who's signed in (Access deployments) — drives the account/sign-out menu.
  loadMe()

  // Server-stored team keys count as configured providers — the pill may
  // appear (or change) once they're known. providerStore reactivity takes
  // care of the refresh; no listeners needed.
  providerStore.loadServerProviders()

  // Load UI feature flags from server config
  try {
    const cfgResp = await http.get('/api/config')
    chatTabEnabled.value = cfgResp.data.enable_chat_tab ?? true
    if (!chatTabEnabled.value && activeTab.value === 'chat') {
      activeTab.value = 'search'
    }
  } catch { /* defaults to true */ }

  // Load user info
  await userStore.loadCurrentUser()

  // Load collections first
  await collectionStore.loadCollections()

  // Then load stats for current collection
  statsStore.fetchStats()

  // Check for any active background jobs
  backgroundJobsStore.checkActiveJobs()

  // Initialize theme
  updateThemeFromStorage()

  // Listen for theme changes (from Settings tab via custom event)
  window.addEventListener('theme-changed', () => {
    updateThemeFromStorage()
  })

  // Listen for storage changes (theme changed in another tab)
  window.addEventListener('storage', (e) => {
    if (e.key === 'theme') {
      updateThemeFromStorage()
    }
  })

  // Listen for system theme changes (only if user hasn't set a preference)
  window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => {
    if (!localStorage.getItem('theme')) {
      updateThemeFromStorage()
    }
  })
})

onBeforeUnmount(() => {
  backgroundJobsStore.cleanup()
})
</script>
