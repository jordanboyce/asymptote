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

      <!-- Active group indicator -->
      <button
        v-if="collectionStore.currentGroupId"
        class="btn btn-xs btn-ghost gap-1.5 normal-case font-normal h-7 min-h-0 text-primary"
        @click="activeTab = 'collections'"
        :title="`Group active: ${collectionStore.currentGroup?.name}. Chat queries across all member collections.`"
      >
        <div
          class="w-2 h-2 rounded-full flex-shrink-0"
          :style="{ backgroundColor: collectionStore.currentGroup?.color || '#8b5cf6' }"
        ></div>
        <Users :size="10" class="opacity-70" />
        <span class="max-w-28 truncate text-xs">{{ collectionStore.currentGroup?.name }}</span>
        <button
          class="ml-0.5 hover:text-error transition-colors text-base-content/50"
          title="Clear group selection"
          @click.stop="collectionStore.setCurrentGroup(null)"
        >×</button>
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

      <!-- Tools dropdown (hidden on collections overview) -->
      <div v-if="!isCollectionsView" class="dropdown dropdown-bottom">
        <label
          tabindex="0"
          class="btn btn-xs btn-ghost gap-1.5 rounded-md transition-all"
          :class="toolTabs.some(t => t.id === activeTab) ? 'bg-base-200 font-semibold' : 'font-normal'"
          aria-label="Tools menu"
          aria-haspopup="menu"
        >
          <Wrench :size="14" />
          <span class="hidden sm:inline">{{ toolTabs.find(t => t.id === activeTab)?.label || 'Tools' }}</span>
          <ChevronDown :size="11" />
        </label>
        <ul tabindex="0" class="dropdown-content z-[100] menu p-1 shadow-lg bg-base-100 border border-base-300 rounded-box w-48">
          <li v-for="tool in toolTabs" :key="tool.id">
            <a
              @click="activeTab = tool.id"
              :class="{ 'active': activeTab === tool.id }"
              class="gap-2 text-sm"
            >
              <component :is="tool.icon" :size="14" />
              {{ tool.label }}
            </a>
          </li>
        </ul>
      </div>

      <!-- Spacer -->
      <div class="flex-1"></div>

      <!-- User identity (multi-user mode) -->
      <div v-if="userStore.isMultiUser" class="hidden md:flex items-center gap-1.5 text-xs text-base-content/50">
        <Users :size="12" />
        <span class="max-w-24 truncate">{{ userStore.displayName }}</span>
      </div>

      <!-- Analysis sidebar toggle (hidden on collections overview) -->
      <button
        v-if="!isCollectionsView"
        class="btn btn-ghost btn-circle btn-sm"
        :class="{ 'bg-base-300': analysisSidebarOpen }"
        @click="analysisSidebarOpen = !analysisSidebarOpen"
        title="Toggle analysis panel"
        aria-label="Toggle analysis panel"
        :aria-pressed="analysisSidebarOpen"
      >
        <PanelRightOpen :size="16" :class="analysisSidebarOpen ? 'rotate-180 transition-transform' : 'transition-transform'" />
      </button>

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
    </header>

    <!-- ── Body ── -->
    <div class="flex flex-1 overflow-hidden min-h-0">

      <!-- Left sidebar: sources (resizable, hidden on collections overview) -->
      <aside
        v-show="!isCollectionsView"
        class="flex-shrink-0 bg-base-100 overflow-hidden flex flex-col relative"
        :class="isResizing ? '' : 'transition-all duration-200 ease-in-out'"
        :style="{ width: sourcesSidebarOpen ? sidebarWidth + 'px' : '0px' }"
      >
        <!-- Sidebar content fixed to sidebarWidth so it doesn't shrink during close animation -->
        <div class="h-full flex flex-col" :style="{ width: sidebarWidth + 'px' }">
          <SourcesSidebar
            @document-deleted="handleDocumentDeleted"
            @background-job-started="showJobsDrawer = true"
            @close="sourcesSidebarOpen = false"
          />
        </div>

        <!-- Drag handle -->
        <div
          v-if="sourcesSidebarOpen"
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
            <ChatTab :chunk-count="stats.chunks" :document-count="stats.documents" @switch-tab="switchTab" />
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
                    @click="openCreateGroupModal"
                  >
                    <Users :size="14" />
                    New group
                  </button>
                  <button
                    class="btn btn-sm btn-ghost gap-1.5 normal-case font-medium border border-base-300 hover:border-base-content/30"
                    @click="openCreateCollectionModal"
                  >
                    <Plus :size="14" />
                    New collection
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
                <li
                  v-for="collection in filteredCollections"
                  :key="collection.id"
                  class="group flex items-start gap-5 py-5 cursor-pointer transition-colors"
                  @click="selectCollectionAndNavigate(collection.id)"
                  :aria-current="collection.id === collectionStore.currentCollectionId ? 'true' : undefined"
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
                        v-if="collectionStore.multiUser && collection.permission === 'owner'"
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
                        v-if="collectionStore.multiUser && collection.permission === 'owner'"
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

              <!-- ── Household Groups ── -->
              <div v-if="collectionStore.groups.length > 0 || showGroupSection" class="mt-10">
                <div class="flex items-center justify-between mb-4">
                  <h2 class="text-sm font-semibold uppercase tracking-[0.1em] text-base-content/50">Household Groups</h2>
                </div>
                <div class="grid gap-3" style="grid-template-columns: repeat(auto-fill, minmax(240px, 1fr));">
                  <div
                    v-for="group in collectionStore.groups"
                    :key="group.id"
                    class="group/grpcard relative flex flex-col gap-2 p-4 rounded-lg border transition-all cursor-pointer"
                    :class="collectionStore.currentGroupId === group.id
                      ? 'border-primary/50 bg-primary/5 ring-1 ring-primary/20'
                      : 'border-base-300/60 bg-base-100 hover:border-base-content/25'"
                    @click="toggleGroupSelection(group.id)"
                    :title="collectionStore.currentGroupId === group.id ? 'Deselect group' : 'Select group for cross-account queries'"
                  >
                    <div class="flex items-center gap-2 min-w-0">
                      <div class="w-3 h-3 rounded-full flex-shrink-0" :style="{ backgroundColor: group.color }"></div>
                      <span class="font-medium text-sm truncate">{{ group.name }}</span>
                      <span v-if="collectionStore.currentGroupId === group.id" class="ml-auto text-[10px] uppercase tracking-wider font-semibold text-primary flex-shrink-0">Active</span>
                    </div>
                    <div class="text-xs text-base-content/50">
                      {{ group.collection_ids.length }} collection{{ group.collection_ids.length !== 1 ? 's' : '' }}
                    </div>
                    <!-- Member chips -->
                    <div class="flex flex-wrap gap-1 mt-1">
                      <span
                        v-for="colId in group.collection_ids"
                        :key="colId"
                        class="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[11px] bg-base-200 text-base-content/70"
                      >
                        <div
                          class="w-1.5 h-1.5 rounded-full flex-shrink-0"
                          :style="{ backgroundColor: collectionById(colId)?.color || '#888' }"
                        ></div>
                        {{ collectionById(colId)?.name || colId }}
                        <button
                          class="ml-0.5 hover:text-error transition-colors"
                          title="Remove from group"
                          @click.stop="collectionStore.removeCollectionFromGroup(group.id, colId)"
                        >×</button>
                      </span>
                    </div>
                    <!-- Add collection dropdown -->
                    <div class="dropdown" @click.stop>
                      <label tabindex="0" class="btn btn-xs btn-ghost gap-1 w-full justify-start mt-1 text-base-content/50 hover:text-base-content">
                        <Plus :size="11" />Add collection
                      </label>
                      <ul tabindex="0" class="dropdown-content z-50 menu p-1 shadow bg-base-100 border border-base-300 rounded-box w-48 max-h-48 overflow-y-auto">
                        <li v-for="col in collectionsNotInGroup(group)" :key="col.id">
                          <a class="text-sm" @click="collectionStore.addCollectionToGroup(group.id, col.id)">
                            <div class="w-2 h-2 rounded-full" :style="{ backgroundColor: col.color }"></div>
                            {{ col.name }}
                          </a>
                        </li>
                        <li v-if="collectionsNotInGroup(group).length === 0">
                          <span class="text-xs text-base-content/40 px-2">All collections added</span>
                        </li>
                      </ul>
                    </div>
                    <!-- Delete group button -->
                    <button
                      class="absolute top-2 right-2 btn btn-ghost btn-xs btn-square opacity-0 group-hover/grpcard:opacity-100 hover:text-error transition-all"
                      title="Delete group"
                      @click.stop="confirmDeleteGroup(group)"
                    >
                      <Trash2 :size="12" />
                    </button>
                  </div>
                </div>
              </div>
            </div>

            <SearchTab v-if="activeTab === 'search'" :chunk-count="stats.chunks" @stats-updated="loadStats" @switch-tab="switchTab" />
            <ExpertiseLibrary v-if="activeTab === 'expertise'" />
            <MCPTab v-if="activeTab === 'mcp'" />
            <OCRPlaygroundTab v-if="activeTab === 'ocr'" @switch-tab="switchTab" />
            <TokenizerTab v-if="activeTab === 'tokenizer'" />
            <SettingsTab v-if="activeTab === 'settings'" @data-cleared="handleDataCleared" @stats-updated="loadStats" @switch-tab="switchTab" @chat-tab-toggled="onChatTabToggled" />
          </div>
        </div>

      </main>

      <!-- Right sidebar: analysis (resizable, hidden on collections overview) -->
      <aside
        v-show="!isCollectionsView"
        class="flex-shrink-0 bg-base-100 overflow-hidden flex flex-col relative border-l border-base-300"
        :class="isResizingAnalysis ? '' : 'transition-all duration-200 ease-in-out'"
        :style="{ width: analysisSidebarOpen ? analysisWidth + 'px' : '0px' }"
      >
        <!-- Drag handle (on the LEFT edge of the right sidebar) -->
        <div
          v-if="analysisSidebarOpen"
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

        <div class="h-full flex flex-col" :style="{ width: analysisWidth + 'px' }">
          <AnalysisSidebar
            @close="analysisSidebarOpen = false"
            @send-to-chat="handleSendToChat"
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

      <!-- Stats moved here from the header for breathing room -->
      <span class="hidden md:inline tabular-nums">
        {{ stats.documents }} {{ stats.documents === 1 ? 'source' : 'sources' }}
      </span>
      <span class="hidden md:inline w-px h-3 bg-base-300" aria-hidden="true"></span>
      <span class="hidden md:inline tabular-nums">
        {{ stats.pages }} {{ stats.pages === 1 ? 'page' : 'pages' }}
      </span>
    </footer>

    <!-- Create Collection Modal -->
    <dialog class="modal" :class="{ 'modal-open': showCollectionModal }" aria-labelledby="create-collection-title">
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
          <button class="btn btn-ghost" @click="showCollectionModal = false" :disabled="creatingCollection">
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
        <button @click="showCollectionModal = false">close</button>
      </form>
    </dialog>

    <!-- Edit Collection Modal -->
    <dialog class="modal" :class="{ 'modal-open': showEditModal }" aria-labelledby="edit-collection-title">
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
            <button class="btn btn-ghost" @click="showEditModal = false" :disabled="updatingCollection">
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
        <button @click="showEditModal = false">close</button>
      </form>
    </dialog>

    <!-- Delete Collection Confirmation Modal -->
    <dialog
      class="modal"
      :class="{ 'modal-open': showDeleteConfirmModal }"
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
        <button @click="closeDeleteModal">close</button>
      </form>
    </dialog>

    <!-- Create Group Modal -->
    <dialog class="modal" :class="{ 'modal-open': showGroupModal }" aria-labelledby="create-group-title">
      <div class="modal-box">
        <h3 id="create-group-title" class="font-bold text-lg mb-4">Create Household Group</h3>
        <p class="text-sm text-base-content/60 mb-4">Groups let you query across multiple collections at once — e.g. all accounts for a household.</p>
        <div class="form-control mb-3">
          <label class="label"><span class="label-text">Group name</span></label>
          <input
            v-model="newGroupName"
            type="text"
            class="input input-bordered"
            placeholder="e.g. Henderson Household"
            @keyup.enter="createGroup"
            autofocus
          />
        </div>
        <div class="form-control mb-4">
          <label class="label"><span class="label-text">Color</span></label>
          <input v-model="newGroupColor" type="color" class="input input-bordered h-10 w-24 p-1 cursor-pointer" />
        </div>
        <div class="modal-action">
          <button class="btn btn-ghost" @click="showGroupModal = false" :disabled="creatingGroup">Cancel</button>
          <button class="btn btn-primary" @click="createGroup" :disabled="creatingGroup || !newGroupName.trim()">
            <span v-if="creatingGroup" class="loading loading-spinner loading-sm"></span>
            Create Group
          </button>
        </div>
      </div>
      <form method="dialog" class="modal-backdrop"><button @click="showGroupModal = false">close</button></form>
    </dialog>

    <!-- Delete Group Confirmation Modal -->
    <dialog class="modal" :class="{ 'modal-open': !!groupToDelete }">
      <div class="modal-box">
        <h3 class="font-bold text-lg text-error mb-2">Delete Group</h3>
        <p class="mb-4">Delete group <strong>{{ groupToDelete?.name }}</strong>? This only removes the grouping — the collections themselves are not affected.</p>
        <div class="modal-action">
          <button class="btn btn-ghost" @click="groupToDelete = null">Cancel</button>
          <button class="btn btn-error" @click="deleteGroupConfirmed">Delete Group</button>
        </div>
      </div>
      <form method="dialog" class="modal-backdrop"><button @click="groupToDelete = null">close</button></form>
    </dialog>

    <!-- Share Modal -->
    <ShareModal
      :visible="showShareModal"
      :collection-id="shareCollectionId"
      :collection-name="shareCollectionName"
      @close="showShareModal = false"
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
      <div class="absolute inset-0 bg-black/30" @click="showJobsDrawer = false" aria-hidden="true"></div>

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
import { ref, onMounted, onBeforeUnmount, watch, computed } from 'vue'
import axios from 'axios'
import { Search, FileText, Settings, Plus, ChevronDown, Pencil, Trash2, Bell, Loader2, CheckCircle, XCircle, X, PanelRightOpen, FileSearch, MessageSquare, Hash, Library, Share2, Users, Wrench, Plug, LayoutGrid, List, BookOpen } from 'lucide-vue-next'

const chatTabEnabled = ref(true)

const tabs = computed(() => {
  const t = []
  if (chatTabEnabled.value) t.push({ id: 'chat', label: 'Chat', icon: MessageSquare })
  t.push({ id: 'search', label: 'Search', icon: Search })
  t.push({ id: 'expertise', label: 'Expertise', icon: BookOpen })
  return t
})

const toolTabs = [
  { id: 'mcp', label: 'MCP Server', icon: Plug },
  { id: 'ocr', label: 'OCR Preview', icon: FileSearch },
  { id: 'tokenizer', label: 'Token Visualizer', icon: Hash },
]
import SearchTab from './components/SearchTab.vue'
import SourcesSidebar from './components/SourcesSidebar.vue'
import AnalysisSidebar from './components/AnalysisSidebar.vue'
import OCRPlaygroundTab from './components/OCRPlaygroundTab.vue'
import TokenizerTab from './components/TokenizerTab.vue'
import ChatTab from './components/ChatTab.vue'
import SettingsTab from './components/SettingsTab.vue'
import MCPTab from './components/MCPTab.vue'
import ShareModal from './components/ShareModal.vue'
import ExpertiseLibrary from './components/ExpertiseLibrary.vue'
import WelcomeOnboarding from './components/WelcomeOnboarding.vue'
import { getConfiguredProviderIds } from './utils/aiProviders.js'
import { useCollectionStore } from './stores/collectionStore'
import { useUserStore } from './stores/userStore'
import { useSearchStore } from './stores/searchStore'
import { useBackgroundJobsStore } from './stores/backgroundJobsStore'

const collectionStore = useCollectionStore()
const searchStore = useSearchStore()
const backgroundJobsStore = useBackgroundJobsStore()
const userStore = useUserStore()

const activeTab = ref('chat')
const currentTheme = ref('light')

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

const stats = ref({
  documents: 0,
  pages: 0,
  chunks: 0
})

// Sources sidebar state (persisted)
const sourcesSidebarOpen = ref(localStorage.getItem('sources_sidebar_open') !== 'false')

watch(sourcesSidebarOpen, v => localStorage.setItem('sources_sidebar_open', String(v)))

// Analysis sidebar state (persisted, resizable)
const ANALYSIS_MIN = 240
const ANALYSIS_MAX = 600
const analysisWidth = ref(parseInt(localStorage.getItem('analysis_width') || '320'))
const analysisSidebarOpen = ref(localStorage.getItem('analysis_sidebar_open') !== 'false')
const isResizingAnalysis = ref(false)

watch(analysisSidebarOpen, v => localStorage.setItem('analysis_sidebar_open', String(v)))

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

// Create collection modal state
const showCollectionModal = ref(false)
const newCollectionName = ref('')
const newCollectionDescription = ref('')
const newCollectionColor = ref('#3b82f6')
const creatingCollection = ref(false)

// Edit collection modal state
const showEditModal = ref(false)
const editingCollectionId = ref('')
const editCollectionName = ref('')
const editCollectionDescription = ref('')
const editCollectionColor = ref('#3b82f6')
const editCollectionGuide = ref('')
const updatingCollection = ref(false)

// Delete confirmation modal state
const showDeleteConfirmModal = ref(false)
const deletingCollection = ref(false)
const deleteError = ref('')

// Share modal state
const showShareModal = ref(false)
const shareCollectionId = ref('')
const shareCollectionName = ref('')

// Background jobs drawer state
const showJobsDrawer = ref(false)

// First-run onboarding: a full-screen takeover shown when the advisor has
// never configured an AI provider. Resolves the "you installed the app but
// nothing works until you curl an endpoint" problem we hit earlier.
const showOnboarding = ref(false)

const checkOnboardingNeeded = () => {
  // If any provider is already configured in localStorage, skip onboarding.
  // We intentionally don't also check server-side embedding keys — the
  // primary gate is "can this advisor have a chat conversation yet."
  // Advisors who pre-configured a chat provider via another path (e.g. the
  // MCP setup flow) shouldn't be blocked by this screen.
  showOnboarding.value = getConfiguredProviderIds().length === 0
}

const handleOnboardingComplete = () => {
  showOnboarding.value = false
  // Nudge any in-flight consumers of the provider config (ChatTab etc.)
  // to refresh their "configured providers" computed state. Vue's reactivity
  // doesn't cover localStorage, so dispatch a synthetic event the
  // components can listen to — or simply rely on re-mount on next tab
  // switch. For now a page-agnostic event is cheapest.
  window.dispatchEvent(new CustomEvent('asymptote:providers-changed'))
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

  return list
})

// ── Group helpers ───────────────────────────────────────────────────────────

const showGroupSection = computed(() => collectionStore.groups.length > 0)

const collectionById = (id) => collectionStore.collections.find(c => c.id === id) || null

const collectionsNotInGroup = (group) =>
  collectionStore.collections.filter(c => !group.collection_ids.includes(c.id))

const toggleGroupSelection = (groupId) => {
  collectionStore.setCurrentGroup(collectionStore.currentGroupId === groupId ? null : groupId)
}

// Create group modal
const showGroupModal = ref(false)
const newGroupName = ref('')
const newGroupColor = ref('#8b5cf6')
const creatingGroup = ref(false)

const openCreateGroupModal = () => {
  newGroupName.value = ''
  newGroupColor.value = '#8b5cf6'
  showGroupModal.value = true
}

const createGroup = async () => {
  if (!newGroupName.value.trim()) return
  creatingGroup.value = true
  try {
    await collectionStore.createGroup(newGroupName.value.trim(), newGroupColor.value)
    showGroupModal.value = false
  } catch (err) {
    console.error('Failed to create group:', err)
  } finally {
    creatingGroup.value = false
  }
}

// Delete group confirmation
const groupToDelete = ref(null)
const confirmDeleteGroup = (group) => { groupToDelete.value = group }
const deleteGroupConfirmed = async () => {
  if (!groupToDelete.value) return
  await collectionStore.deleteGroup(groupToDelete.value.id)
  groupToDelete.value = null
}

const updateThemeFromStorage = () => {
  const savedTheme = localStorage.getItem('theme')
  if (savedTheme) {
    currentTheme.value = savedTheme
    document.documentElement.setAttribute('data-theme', savedTheme)
  } else {
    const prefersDark = window.matchMedia('(prefers-color-scheme: dark)').matches
    currentTheme.value = prefersDark ? 'dark' : 'light'
    document.documentElement.setAttribute('data-theme', currentTheme.value)
  }
}

const loadStats = async () => {
  try {
    const collectionId = collectionStore.currentCollectionId
    const [docsResponse, healthResponse] = await Promise.all([
      axios.get(`/documents?collection_id=${collectionId}`),
      axios.get(`/health?collection_id=${collectionId}`)
    ])

    stats.value.documents = docsResponse.data.documents?.length || 0
    stats.value.pages = docsResponse.data.documents?.reduce((sum, doc) => sum + (doc.total_pages || 0), 0) || 0
    stats.value.chunks = healthResponse.data.indexed_chunks || 0
  } catch (error) {
    console.error('Error loading stats:', error)
  }
}

const handleDocumentDeleted = async () => {
  // Reload both stats and collections list (for document_count in dropdown)
  await Promise.all([
    loadStats(),
    collectionStore.loadCollections()
  ])
}

const handleDataCleared = async () => {
  // Reload both stats and collections list (for document_count in dropdown)
  await Promise.all([
    loadStats(),
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

const selectCollection = (collectionId) => {
  collectionStore.setCurrentCollection(collectionId)
}

const selectCollectionAndNavigate = (collectionId) => {
  collectionStore.setCurrentCollection(collectionId)
  activeTab.value = chatTabEnabled.value ? 'chat' : 'search'
}

const openCreateCollectionModal = () => {
  newCollectionName.value = ''
  newCollectionDescription.value = ''
  newCollectionColor.value = '#3b82f6'
  showCollectionModal.value = true
}

const createCollection = async () => {
  if (!newCollectionName.value.trim()) return

  creatingCollection.value = true
  try {
    const collection = await collectionStore.createCollection({
      name: newCollectionName.value.trim(),
      description: newCollectionDescription.value.trim(),
      color: newCollectionColor.value
    })
    collectionStore.setCurrentCollection(collection.id)
    showCollectionModal.value = false
  } catch (err) {
    console.error('Failed to create collection:', err)
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
  showEditModal.value = true
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
    showEditModal.value = false
  } catch (err) {
    console.error('Failed to update collection:', err)
  } finally {
    updatingCollection.value = false
  }
}

const openShareModal = (collection) => {
  shareCollectionId.value = collection.id
  shareCollectionName.value = collection.name
  showShareModal.value = true
}

const handleShared = () => {
  collectionStore.loadCollections()
}

const confirmDeleteCollection = () => {
  deleteError.value = ''
  showDeleteConfirmModal.value = true
}

const closeDeleteModal = () => {
  if (!deletingCollection.value) {
    showDeleteConfirmModal.value = false
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
    showDeleteConfirmModal.value = false
    showEditModal.value = false
    deleteError.value = ''

    // Switch to default collection if we deleted the current one
    if (wasCurrentCollection) {
      collectionStore.setCurrentCollection('default')
    }

    // Reload stats for the new current collection
    await loadStats()
  } catch (err) {
    console.error('Failed to delete collection:', err)
    deleteError.value = err.response?.data?.detail || err.message || 'Failed to delete collection. Please try again.'
  } finally {
    deletingCollection.value = false
  }
}

// Watch for collection changes to reload stats
watch(() => collectionStore.currentCollectionId, () => {
  loadStats()
})

// Watch for background jobs completing to refresh collection counts and stats
watch(() => backgroundJobsStore.allJobs.map(j => j.status), (newStatuses, oldStatuses) => {
  // Check if any job just transitioned to completed
  if (oldStatuses && newStatuses.some((s, i) => s === 'completed' && oldStatuses[i] !== 'completed')) {
    collectionStore.loadCollections()
    loadStats()
  }
}, { deep: true })

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
    console.error('Failed to cancel job:', err)
  }
}

// Theme change handlers — referenced by add/removeEventListener so they must
// be the same function identity on both sides.
const onThemeChanged = () => updateThemeFromStorage()
const onStorageThemeChanged = (e) => {
  if (e.key === 'theme') updateThemeFromStorage()
}
const onSystemThemeChanged = () => {
  if (!localStorage.getItem('theme')) updateThemeFromStorage()
}
let prefersDarkMedia = null

onMounted(async () => {
  // Check whether we need the first-run onboarding takeover. Done first so
  // the screen paints immediately — the rest of the boot continues behind it.
  checkOnboardingNeeded()

  // Load UI feature flags from server config
  try {
    const cfgResp = await axios.get('/api/config')
    chatTabEnabled.value = cfgResp.data.enable_chat_tab ?? true
    if (!chatTabEnabled.value && activeTab.value === 'chat') {
      activeTab.value = 'search'
    }
  } catch { /* defaults to true */ }

  // Load user info
  await userStore.loadCurrentUser()

  // Load collections and groups
  await collectionStore.loadCollections()
  collectionStore.loadGroups()

  // Then load stats for current collection
  loadStats()

  // Check for any active background jobs
  backgroundJobsStore.checkActiveJobs()

  // Initialize theme
  updateThemeFromStorage()

  // Theme listeners — named so onBeforeUnmount can detach them
  window.addEventListener('theme-changed', onThemeChanged)
  window.addEventListener('storage', onStorageThemeChanged)
  prefersDarkMedia = window.matchMedia('(prefers-color-scheme: dark)')
  prefersDarkMedia.addEventListener('change', onSystemThemeChanged)
})

onBeforeUnmount(() => {
  window.removeEventListener('theme-changed', onThemeChanged)
  window.removeEventListener('storage', onStorageThemeChanged)
  prefersDarkMedia?.removeEventListener('change', onSystemThemeChanged)
  backgroundJobsStore.cleanup()
})
</script>
